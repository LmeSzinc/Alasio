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
alasio.deploy_dev.compile.pack_deps.resolve. A version is a pack target when it
publishes a pure python wheel and no compiled wheel the target interpreter can
install: the pack channel builds its packs from that one wheel, so declaring a
version that also ships a compiled build would replace the build a client
installs today with the pure fallback on every platform. The answer is decided
once, by the mod author, and written into the declaration: the runtime never
reads an index for it, the declaration is the record.

The tool walks the closure of the distributions the caller registers, over the
requirements file of the mod (which is the lock: every pin is an exact
version), and replaces the whole ``pack_deps`` of the entry with the names it
decided to be pack targets. Nothing else in the declaration is touched.

Usage:
    from alasio.deploy_dev.compile.pack_deps.manager import PackDepsManager

    manager = PackDepsManager()                      # env.PROJECT_ROOT
    manager.register('alasio[backend,image]', recursive=True)
    print(manager.resolve())                         # the decisions, no write
    manager.write()                                  # replace Update.pack_deps

The interpreter that runs the tool is the target of the decisions: the wheels
are matched against ``sys.version_info``. A mod author that develops on 3.11
and ships a 3.8 runtime should run the tool inside the runtime environment,
otherwise a dependency that is compiled for 3.9+ only is left on the pack
channel (a conservative miss, not a break).

The design of the tool, and why the runtime does not derive the list itself, is
in doc/2026-11-15_pack-deps-manager.md.
"""

import sys

import httpx2
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from trio import open_nursery, run as trio_run

from alasio.config.entry.mod_entry import ModEntry
from alasio.deploy.httpclient.probe_mirrors import AllMirrorsFailedError
from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy.pip_download.pypi_file import WheelNotFoundError
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy_dev.compile.pack_deps.resolve import (
    DECISION_PACK, DECISION_PIP, REASON_FORCED, REASON_INDEX, REASON_NO_WHEEL, PackDepDecision, decide, decision_text,
    wheels_of
)
from alasio.deploy_dev.metadata import ClosureRoot, MetadataProvider
from alasio.deploy_dev.pack_server.fetch_wheel import create_client
from alasio.ext import env
from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_text
from alasio.logger import logger

__all__ = ['PackDepsError', 'PackDepsManager']


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

    # Requests in flight of one resolution (see alasio.deploy_dev.metadata):
    # the closure of a mod is a few tens of distributions, each one page and one
    # metadata, and both are small requests
    CONCURRENT = 8

    def __init__(self, entry=None, client=None, proxy='', provider=None):
        """
        Args:
            entry (ModEntry, optional): The declaration to maintain. Defaults to
                None, the mod declared by env.PROJECT_ROOT
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client to
                send the requests with. Defaults to None, a client of the run
            proxy (str): Proxy url the requests are routed through, e.g.
                'http://127.0.0.1:7890'. Defaults to '', a direct connection
            provider (MetadataProvider, optional): The metadata reader of a
                run that reads several files, e.g. a compile followed by this
                tool: the pages and the METADATA the compile read are already
                in its caches, so the run costs no request for them. Defaults
                to None, a provider of this run, closed with it

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
        self._given_provider = provider
        # The folder a fetcher would write a wheel into. The tool reads every
        # file in memory (see alasio.deploy_dev.metadata), so nothing is written
        # today; the fetcher still takes the folder
        self.dest = PathStr.new(entry.root or env.PROJECT_ROOT).joinpath('temp/pack_deps')
        self._roots = []
        # the state of the run in progress, see _resolve()
        self._provider = None
        self._states = {}

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

        The walk is the one of the metadata provider: a name expands its
        dependencies as soon as its METADATA is read, so the rounds of requests
        follow the depth of the closure rather than its width, and the pages
        and the METADATA of the run are shared with the tool that reads them
        next (see alasio.deploy_dev.metadata).

        Returns:
            dict[str, PackDepDecision]: PEP 503 name -> decision
        """
        if not self._roots:
            raise PackDepsError('No distribution is registered, call register() first')
        logger.info(
            f'Resolving the pack_deps of "{self.entry.name}" for python '
            f'{self.target.py_version} ({len(self._roots)} registered)')

        self._errors = {}
        # the roots merged per name: a name is decided once, whatever the
        # number of roots that asked for it
        states = {}
        for root in self._roots:
            self._merge(states, root)
        self._states = states

        own = self.client is None and self._given_provider is None
        if self._given_provider is not None:
            provider = self._given_provider
            client = None
        else:
            # the client of the pack server of the domain: a direct connection,
            # or the proxy the operator gives (the backport indexes serve their
            # wheels from GitHub releases, which some networks cannot reach)
            client = create_client(self.proxy) if own else self.client
            provider = MetadataProvider(
                client=client, concurrency=self.CONCURRENT, dest=self.dest)
        self._provider = provider
        try:
            roots = [
                ClosureRoot(node.name, frozenset(node.extras), node.recursive)
                for node in self._roots
            ]
            closure = await provider.closure(roots, self._pin_of, self.target)
            self._errors = dict(closure.errors)
            self._report_missing(closure.missing)
            decisions = await self._decide_all(closure.names)
        finally:
            self._provider = None
            if own:
                await client.aclose()
        if self._errors:
            logger.error(
                f'The closure of {len(self._errors)} distribution(s) is incomplete, write() '
                f'refuses it: {", ".join(sorted(self._errors))}')
        return decisions

    def _pin_of(self, name):
        """
        The pin of a distribution, the shape the closure walk asks for.

        Args:
            name (str): PEP 503 name

        Returns:
            tuple[str, str] | None: (version, index) of the pin, None when the
                requirements file does not pin the name
        """
        pin = self.pins.get(name)
        if pin is None:
            return None
        return pin.version, pin.source

    def _report_missing(self, missing):
        """
        Report the dependencies the requirements file does not pin.

        Args:
            missing (Iterable[str]): PEP 503 names
        """
        if not missing:
            return
        logger.warning(
            f'Dependencies of the closure are not pinned in "{self.path_requirements}", '
            f'skipped: {", ".join(sorted(missing))}')

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

    async def _decide_all(self, names):
        """
        Decide the distributions of the closure, their index pages in parallel.

        The pages are the ones the closure already read when it needed them, so
        a decision costs no request of its own for a name the closure expanded.

        Args:
            names (Iterable[str]): The PEP 503 names of the closure

        Returns:
            dict[str, PackDepDecision]: PEP 503 name -> decision
        """
        out = {}

        async def one(name):
            out[name] = await self._decide(name)

        async with open_nursery() as nursery:
            for name in names:
                nursery.start_soon(one, name)

        # the log of the run is sorted, the answers of the requests are not
        for name in sorted(out):
            logger.info(decision_text(out[name]))
        return out

    async def _decide(self, name):
        """
        Decide one distribution.

        Args:
            name (str): PEP 503 name

        Returns:
            PackDepDecision: The decision, ``pip`` with the reason when the
                index cannot be read (the write refuses those, see write_async)
        """
        pin = self.pins[name]
        state = self._states.get(name)
        if state is not None and state.force:
            return PackDepDecision(
                name=name, version=pin.version, decision=DECISION_PACK, reason=REASON_FORCED)

        try:
            page = await self._provider.page(name, pin.source)
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
