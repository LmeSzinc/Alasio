"""
Maintain ``Update.pack_deps`` of a mod entry from its requirements file.

A mod updates the distributions its requirements file pins through two
channels: the pack channel of the mod (the distributions declared in
``Update.pack_deps``) and pip (every other pin, by the flow of the caller). The
usage side of the split is in alasio.deploy.simple_pip.install_base: the pack
channel serves the pins its snapshot carries, and the declared names the
snapshot does not carry are refused.

Which distribution belongs to which channel is a property of the wheels a
pinned version publishes, and that is what this tool reads, see
alasio.deploy_dev.pack_deps.resolve. The answer is decided once, by the mod
author, and written into the declaration: the runtime never reads an index for
it, the declaration is the record.

The tool walks the closure of the distributions the caller registers, over the
requirements file of the mod (which is the lock: every pin is an exact
version), and replaces the whole ``pack_deps`` of the entry with the names it
decided to be pack targets. Nothing else in the declaration is touched.

Usage:
    from alasio.deploy_dev.pack_deps import PackDepsManager

    manager = PackDepsManager()                      # env.PROJECT_ROOT
    manager.register('alasio[backend,image]', recursive=True)
    print(manager.resolve())                         # the decisions, no write
    manager.write()                                  # replace Update.pack_deps

The interpreter that runs the tool is the target of the decisions: the wheels
are matched against ``sys.version_info``. A mod author that develops on 3.11
and ships a 3.8 runtime should run the tool inside the runtime environment,
otherwise a dependency that is compiled for 3.9+ only is left on the pack
channel (a conservative miss, not a break).
"""

import sys
from collections import deque

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from trio import CapacityLimiter, open_nursery, run as trio_run

from alasio.config.entry.mod_entry import ModEntry
from alasio.deploy.httpclient.probe_mirrors import AllMirrorsFailedError
from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy.pip_download.pypi_file import PypiFile, WheelNotFoundError
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy_dev.pack_deps.closure import read_metadata, requirement_names, requires_dist
from alasio.deploy_dev.pack_deps.resolve import (
    DECISION_PACK, DECISION_PIP, REASON_FORCED, REASON_INDEX, REASON_NO_WHEEL, PackDepDecision, decide, decision_text,
    wheels_of
)
from alasio.deploy_dev.pack_server.fetch_wheel import create_client
from alasio.ext import env
from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_text
from alasio.logger import logger

__all__ = ['PackDepsError', 'PackDepsManager']

# The index a pin without a '# from' annotation is read from: the official PyPI
# simple index, the value of RepoBase.DEFAULT_INDEX. The requirements file is
# the only source of an index, see PackDepsManager._fetcher_of
DEFAULT_INDEX = 'https://pypi.org/simple'


class PackDepsError(ValueError):
    """
    Raised when the pack_deps of a mod cannot be resolved or written.

    The message names the distribution or the file the caller has to fix; no
    value is replaced silently.
    """


class _Node:
    """
    One distribution of the walk, and what the caller asked for it.

    Attributes:
        name (str): PEP 503 normalized name
        extras (set[str]): Extras to expand, the empty extra is the base
            dependencies and is always there
        recursive (bool): True to expand the dependencies of the distribution
        force (bool): True to declare the distribution without reading its
            index
    """

    __slots__ = ('extras', 'force', 'name', 'recursive')

    def __init__(self, name, extras=(), recursive=False, force=False):
        self.name = name
        self.extras = {'', *extras}
        self.recursive = recursive
        self.force = force


class PackDepsManager:
    """
    Resolve and write the pack_deps of one mod.

    One instance is one run of the tool: the requirements file and the
    declaration are read once, the index pages of the closure are read during
    resolve(), and write() replaces the field of the declaration.

    Attributes:
        entry (ModEntry): The declaration being maintained
        client (httpx2.AsyncClient | AsyncHttpClient | None): Client the index
            requests are sent with, None to create one for the run
        proxy (str): Proxy the requests of the run are routed through, '' for a
            direct connection
        dest (PathStr): Folder a fetcher would write a wheel into, see __init__
    """

    # Index pages in flight of one resolution: the closure of a mod is a few
    # tens of distributions, one page each, and a page is a small request
    CONCURRENT = 8

    def __init__(self, entry=None, client=None, proxy=''):
        """
        Args:
            entry (ModEntry, optional): The declaration to maintain. Defaults to
                None, the mod declared by env.PROJECT_ROOT
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client to
                send the requests with. Defaults to None, a client of the run
            proxy (str): Proxy url the requests are routed through, e.g.
                'http://127.0.0.1:7890'. Defaults to '', a direct connection

        Raises:
            PackDepsError: If the interpreter is not CPython (the wheel tags are
                matched against a CPython target), or no project root is set
        """
        implementation = sys.implementation.name
        if implementation != 'cpython':
            raise PackDepsError(
                f'The tool resolves the wheels of CPython, this process runs "{implementation}"')
        if entry is None:
            if not env.PROJECT_ROOT:
                raise PackDepsError(
                    'PROJECT_ROOT is not set, call env.set_project_root() first or pass entry=')
            entry = ModEntry(env.PROJECT_ROOT)
        self.entry = entry
        self.client = client
        self.proxy = proxy
        # The folder a fetcher would write a wheel into. The tool reads every
        # file in memory (see closure.read_metadata), so nothing is written
        # today; the fetcher still takes the folder, and the mod's own temp is
        # the place a future full-wheel read would use
        self.dest = PathStr.new(entry.root or env.PROJECT_ROOT).joinpath('temp/pack_deps')
        self._roots = []
        # the state of the run in progress, see resolve_async()
        self._client = None
        self._fetchers = {}
        self._limiter = None
        self._pages = {}

    def __repr__(self):
        return f'{self.__class__.__name__}(root="{self.entry.root}")'

    @property
    def errors(self):
        """
        The distributions whose dependencies could not be read by the last run.

        A closure that stops at such a name is incomplete, and a write of it
        would drop the names behind it, so write() refuses the run, see
        write_async().

        Returns:
            dict[str, str]: {PEP 503 name: reason}, empty when every METADATA of
                the closure was read
        """
        return dict(getattr(self, '_errors', None) or {})

    """
    The declarations
    """

    @cached_property
    def path_requirements(self):
        """
        Returns:
            PathStr: {entry.root}/{entry.requirements_file}
        """
        # ModEntry.root is a plain path string, not a PathStr
        return PathStr.new(self.entry.root).joinpath(self.entry.requirements_file)

    @cached_property
    def pins(self):
        """
        The pins of the requirements file, the lock the closure is read over.

        Returns:
            dict[str, DepInfo]: PEP 503 name -> pinned version and the index it
                came from

        Raises:
            PackDepsError: If the mod has no requirements file
        """
        try:
            content = atomic_read_text(self.path_requirements)
        except FileNotFoundError:
            raise PackDepsError(
                f'The mod has no requirements file: "{self.path_requirements}"') from None
        return Requirements(content).dict_pins

    @cached_property
    def target(self):
        """
        The interpreter the wheels are decided for: the one running the tool.

        Returns:
            TargetPython: The target interpreter
        """
        return TargetPython()

    def register(self, requirement, recursive=False, force=False):
        """
        Register a root of the closure.

        The requirement names a distribution of the requirements file; the
        version is never declared here, it is the pin of the file. A
        distribution that is not pinned is refused at once: the pack channel
        installs the pinned version, so a name outside the lock would be
        declared and never used.

        Args:
            requirement (str): PEP 508 text without a version, a url or a
                marker, e.g. 'alasio[backend,image]'
            recursive (bool): True to walk the dependencies of the
                distribution. Defaults to False
            force (bool): True to declare the distribution without reading its
                index, for a private package or an index this machine cannot
                reach. Defaults to False

        Returns:
            str: The PEP 503 normalized name

        Raises:
            PackDepsError: If the requirement is not a plain name, or the name
                is not pinned by the requirements file
        """
        requirement = self._parse_requirement(requirement)
        name = canonicalize_name(requirement.name)
        if name not in self.pins:
            raise PackDepsError(
                f'"{name}" is not pinned in "{self.path_requirements}": add it to the '
                f'lock first, the pack channel installs the pinned version only')
        self._roots.append(_Node(
            name, requirement.extras, recursive=recursive, force=force))
        logger.debug(f'Registered "{name}", recursive={recursive}, force={force}')
        return name

    @staticmethod
    def _parse_requirement(text):
        """
        Parse the requirement of a root.

        Args:
            text (str): PEP 508 text, e.g. 'alasio[backend]'

        Returns:
            Requirement: The parsed requirement

        Raises:
            PackDepsError: If the text is not a requirement, or it carries a
                version, a url or a marker
        """
        try:
            requirement = Requirement(text)
        except InvalidRequirement as e:
            raise PackDepsError(f'Invalid requirement "{text}": {e}') from None
        if requirement.url or requirement.specifier or requirement.marker:
            raise PackDepsError(
                f'The requirement "{text}" carries a version, a url or a marker: the version '
                f'of a distribution is the one its requirements file pins')
        return requirement

    """
    The resolution
    """

    def resolve(self):
        """
        Resolve the closure of the registered distributions.

        Returns:
            dict[str, PackDepDecision]: PEP 503 name -> decision, every name of
                the closure

        Raises:
            PackDepsError: If no distribution is registered
        """
        return trio_run(self.resolve_async)

    async def resolve_async(self):
        """
        Resolve the closure, see resolve().

        Returns:
            dict[str, PackDepDecision]: PEP 503 name -> decision
        """
        return await self._resolve()

    async def _resolve(self):
        """
        Walk the closure and decide every distribution of it.

        A breadth first walk: the names of a level are decided together, the
        dependencies of each decided name are read from its METADATA and become
        the next level. A name reached twice with a new extra is read again, so
        the dependencies of that extra are in the closure.

        Returns:
            dict[str, PackDepDecision]: PEP 503 name -> decision
        """
        if not self._roots:
            raise PackDepsError('No distribution is registered, call register() first')
        logger.info(
            f'Resolving the pack_deps of "{self.entry.name}" for python '
            f'{self.target.py_version} ({len(self._roots)} registered)')

        self._pages = {}
        self._fetchers = {}
        self._errors = {}
        self._limiter = CapacityLimiter(self.CONCURRENT)

        states = {}
        expanded = {}
        queue = deque()
        for root in self._roots:
            if self._merge(states, root):
                queue.append(root.name)

        own = self.client is None
        # the client of the pack server of the domain: a direct connection, or
        # the proxy the operator gives (the backport indexes serve their wheels
        # from GitHub releases, which some networks cannot reach)
        client = create_client(self.proxy) if own else self.client
        self._client = client
        try:
            decisions = await self._walk(queue, states, expanded)
        finally:
            self._client = None
            if own:
                await client.aclose()
        if self._errors:
            logger.error(
                f'The closure of {len(self._errors)} distribution(s) is incomplete, write() '
                f'refuses it: {", ".join(sorted(self._errors))}')
        return decisions

    @staticmethod
    def _merge(states, node):
        """
        Merge a node into the state of the walk.

        Args:
            states (dict[str, _Node]): The state of every name seen so far
            node (_Node): The node to merge

        Returns:
            bool: True when the name is new, or its extras grew
        """
        state = states.get(node.name)
        if state is None:
            states[node.name] = node
            return True
        before = len(state.extras)
        state.extras |= node.extras
        state.recursive = state.recursive or node.recursive
        state.force = state.force or node.force
        return len(state.extras) != before

    async def _walk(self, queue, states, expanded):
        """
        Drain the work list of the walk.

        Args:
            queue (deque[str]): Names to decide, then to expand
            states (dict[str, _Node]): The state of every name seen so far
            expanded (dict[str, set[str]]): {name: extras already expanded}

        Returns:
            dict[str, PackDepDecision]: PEP 503 name -> decision
        """
        decisions = {}
        while queue:
            batch = []
            while queue:
                name = queue.popleft()
                state = states[name]
                undecided = name not in decisions
                unexpanded = state.recursive and not state.extras <= expanded.get(name, set())
                if undecided or unexpanded:
                    batch.append(name)
            if not batch:
                break

            pending = [name for name in batch if name not in decisions]
            if pending:
                decisions.update(await self._decide_many(pending, states))

            for children in await self._expand_many(batch, states, expanded):
                for child in children:
                    if self._merge(states, child):
                        queue.append(child.name)
        return decisions

    async def _expand_many(self, names, states, expanded):
        """
        Expand the dependencies of a level, the metadata reads in parallel.

        A name whose extras grew since its last expansion is read again, so the
        dependencies of a new extra join the closure; the bookkeeping is done
        before the reads, so a name is never expanded twice for one extra.

        Args:
            names (list[str]): The names of the level
            states (dict[str, _Node]): The state of every name seen so far
            expanded (dict[str, set[str]]): {name: extras already expanded}

        Returns:
            list[list[_Node]]: The dependencies of every name that was
                expanded, in the order of the level
        """
        todo = []
        for name in names:
            state = states[name]
            new = state.extras - expanded.get(name, set())
            expanded.setdefault(name, set()).update(state.extras)
            if state.recursive and new:
                todo.append((name, new))

        out = {}

        async def one(name, extras):
            out[name] = await self._children_of(name, extras)

        async with open_nursery() as nursery:
            for name, extras in todo:
                nursery.start_soon(one, name, extras)
        return [out[name] for name, _ in todo]

    async def _decide_many(self, names, states):
        """
        Decide a level of the walk, its index pages in parallel.

        Args:
            names (list[str]): The names to decide
            states (dict[str, _Node]): The state of every name seen so far

        Returns:
            dict[str, PackDepDecision]: PEP 503 name -> decision
        """
        out = {}

        async def one(name):
            out[name] = await self._decide(name, states[name])

        async with open_nursery() as nursery:
            for name in names:
                nursery.start_soon(one, name)

        # the log of a level is sorted, the answers of the requests are not
        for name in sorted(out):
            logger.info(decision_text(out[name]))
        return out

    async def _decide(self, name, state):
        """
        Decide one distribution.

        Args:
            name (str): PEP 503 name
            state (_Node): What the caller asked for the name

        Returns:
            PackDepDecision: The decision, ``pip`` with the reason when the
                index cannot be read (the write refuses those, see write_async)
        """
        pin = self.pins[name]
        if state.force:
            return PackDepDecision(
                name=name, version=pin.version, decision=DECISION_PACK, reason=REASON_FORCED)

        # httpx2 is imported lazily in the places that catch its errors, see
        # the note of alasio.deploy.simple_pip.install_base
        import httpx2

        try:
            async with self._limiter:
                page = await self._page_of(name, pin.source)
        except (ValueError, AllMirrorsFailedError, httpx2.HTTPError) as e:
            logger.warning(f'Failed to read the index of "{name}": {e}')
            return PackDepDecision(
                name=name, version=pin.version, decision=DECISION_PIP,
                reason=REASON_INDEX, source=pin.source)

        try:
            candidates = wheels_of(page.links, name, pin.version)
        except WheelNotFoundError as e:
            logger.warning(f'"{name}=={pin.version}" publishes no wheel: {e}')
            return PackDepDecision(
                name=name, version=pin.version, decision=DECISION_PIP,
                reason=REASON_NO_WHEEL, source=page.url)

        return decide(name, pin.version, page.url, candidates, self.target)

    async def _children_of(self, name, extras):
        """
        The dependencies of one decided distribution, for some extras.

        A read that fails is recorded in the errors of the run: the closure of
        a distribution is unknown then, and an unknown closure must not shrink
        the declaration silently, see write_async().

        Args:
            name (str): PEP 503 name
            extras (set[str]): The extras to expand, the empty one is the base

        Returns:
            list[_Node]: The dependencies pinned by the requirements file. A
                dependency the lock does not pin is reported and dropped
        """
        pin = self.pins[name]
        # httpx2 is imported lazily, the metadata fallback downloads a wheel
        import httpx2

        try:
            async with self._limiter:
                page = await self._page_of(name, pin.source)
                candidates = wheels_of(page.links, name, pin.version)
                content, origin = await read_metadata(
                    self._fetcher_of(pin.source), candidates)
        except (ValueError, AllMirrorsFailedError, httpx2.HTTPError) as e:
            logger.error(
                f'Failed to read the dependencies of "{name}=={pin.version}", the closure '
                f'is incomplete: {e}')
            self._errors[name] = str(e)
            return []

        logger.debug(f'Metadata of "{name}=={pin.version}": "{origin}"')
        children = requirement_names(requires_dist(content), extras, self.target)
        missing = [child for child in children if child not in self.pins]
        if missing:
            logger.warning(
                f'Dependencies of "{name}=={pin.version}" are not pinned in '
                f'"{self.path_requirements}", skipped: {", ".join(sorted(missing))}')
        return [
            _Node(child, wanted, recursive=True)
            for child, wanted in children.items()
            if child in self.pins
        ]

    """
    The index
    """

    async def _page_of(self, name, source):
        """
        The index page of a distribution, read once per run.

        Args:
            name (str): PEP 503 name
            source (str): Index the pin came from, '' for a pin without a
                '# from' annotation

        Returns:
            PackageIndex: The page and its links

        Raises:
            ValueError: If the index has no page of the distribution, or the
                page does not parse
            AllMirrorsFailedError: If no mirror is usable
            httpx2.HTTPError: If a request fails
        """
        key = (name, source)
        page = self._pages.get(key)
        if page is None:
            page = await self._fetcher_of(source).get_package_index(name)
            self._pages[key] = page
        return page

    def _fetcher_of(self, source):
        """
        The fetcher of one index, created once per run.

        The index of a pin is the one its '# from' annotation names, and the
        official PyPI when the pin has none: the same rule the pack server
        follows (see RepoBase.DEFAULT_INDEX). The requirements file is the only
        source of that, the pypi_mirrors of the mod entry is not read: that is
        the source a client installs from, and it may point at the pack server
        of this very mod, so the tool would read a mirror of its own output.

        A pin with a source gets that index alone, so no probe selects between
        candidates and the record of the machine is not used.

        Args:
            source (str): Normalized base url of an index, '' for the pin
                without an annotation

        Returns:
            PypiFile: The fetcher
        """
        fetcher = self._fetchers.get(source)
        if fetcher is not None:
            return fetcher
        fetcher = PypiFile(source or DEFAULT_INDEX, self.dest, client=self._client)
        self._fetchers[source] = fetcher
        return fetcher

    """
    The declaration
    """

    def write(self, gitadd=None):
        """
        Resolve the closure and replace ``Update.pack_deps`` with the answer.

        Args:
            gitadd (GitAdd, optional): Stage the file when it changed. Defaults
                to None, the file is only written

        Returns:
            bool: True when the file was written, False when it already held
                the resolved list

        Raises:
            PackDepsError: If no distribution is registered, or the index of a
                distribution cannot be read (nothing is written then, a
                network failure must not empty the declaration)
            ModEntryError: If the declaration cannot be written
        """
        return trio_run(self.write_async, gitadd)

    async def write_async(self, gitadd=None):
        """
        Resolve the closure and write the declaration, see write().

        Args:
            gitadd (GitAdd, optional): Stage the file when it changed

        Returns:
            bool: True when the file was written
        """
        decisions = await self._resolve()
        unknown = sorted(
            name for name, decision in decisions.items() if decision.reason == REASON_INDEX)
        if unknown:
            raise PackDepsError(
                f'The index of {len(unknown)} distribution(s) cannot be read, nothing is '
                f'written: {", ".join(unknown)}. Check the network, or register(..., '
                f'force=True) to declare a distribution the index does not serve')
        if self._errors:
            broken = ', '.join(sorted(self._errors))
            raise PackDepsError(
                f'The dependencies of {len(self._errors)} distribution(s) cannot be read, '
                f'nothing is written: {broken}. The closure is incomplete, a write would '
                f'drop the names behind them. Check the network of their indexes')

        names = sorted(name for name, decision in decisions.items() if decision.pack)
        entry = self.entry
        old = {canonicalize_name(name) for name in entry.data.Update.pack_deps}
        added = [name for name in names if name not in old]
        removed = sorted(old - set(names))

        entry.data.Update.pack_deps = tuple(names)
        # the views of the entry are cached: the caller that reads the entry
        # after the write must see the value this run decided
        InstanceCacheOperation.pop(entry, 'pack_deps')

        changed = entry.write(gitadd=gitadd)
        logger.info(
            f'pack_deps of "{entry.name}": {len(names)} distribution(s), '
            f'{len(added)} added, {len(removed)} removed')
        if added:
            logger.info(f'Added: {", ".join(added)}')
        if removed:
            logger.info(f'Removed: {", ".join(removed)}')
        if not changed:
            logger.info(f'The declaration already holds the answer: "{entry.file}"')
        return changed
