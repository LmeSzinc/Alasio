"""
Compile the dependencies of a project into the pins a client installs.

The compiler reads the dependency file of a project (``pyproject.toml``), walks
the requirements through the indexes, and writes the pins into a requirements
file: the same job as ``uv pip compile``, with the rules of this project (a
wheel of the target platform, no sdist, no URL dependency) and without an
external binary.

It is the developer side of the pack channel: the file it writes is the lock
``pack_deps`` reads to decide the pack channel, and the lock a client installs
the dependencies from (alasio.deploy.simple_pip). The two tools share the
metadata reader (alasio.deploy_dev.metadata), so a compile followed by a
``PackDepsManager`` run in the same process reads every page and every METADATA
once.

What the compiler reads from the project:

- the requirements: ``[project].dependencies``, the optional dependency groups
  the caller names (``[project.optional-dependencies]``), and the dependency
  groups of ``[dependency-groups]`` (PEP 735). A poetry project
  (``[tool.poetry]``) is read when the document has no ``[project]`` table;
- the indexes: the official PyPI index, then every ``[[tool.uv.index]]`` of the
  document. An index declared with ``default = true`` replaces PyPI as the
  primary one. A distribution whose ``[tool.uv.sources]`` entry names an index
  is read from that index only;
- the interpreter: the running one unless the caller names another python
  version. The lock is compiled for one platform, the platform of the machine
  that compiles it, exactly like ``uv pip compile``: a dependency that exists
  for one platform only is in the lock of that platform only.

What it does not do (see doc/2026-11-16_own-uv-compile.md):

- no sdist: a version without a wheel for the target is not a candidate, and a
  constraint whose every version is an sdist fails with a message that names
  them;
- no URL, git or local path dependency: refused rather than guessed;
- no universal (cross platform) resolution: the lock is one platform, the
  union of the platforms is what ``pack_deps`` decides over.

Usage:
    from alasio.deploy_dev.compile import RequirementsCompile

    with RequirementsCompile(root=env.PROJECT_ROOT, header='# generated') as job:
        result = job.compile()
        result.pins                                  # {'httpx2': '2.16.0', ...}
        result.written                               # False when unchanged

        # the pack channel of the same mod, over the metadata the compile read
        job.pack_deps.register('alasio[backend,image]', recursive=True)
        job.pack_deps.write()

The block closes the reader and the client of the run, so a caller writes the
``with`` and never calls aclose(); an async caller uses ``async with``.
"""

import functools
from typing import Dict, List

import trio
from msgspec import Struct
from packaging.utils import canonicalize_name

from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy.simple_pip.parse_req import Requirements
from alasio.deploy_dev.compile.candidates import Index
from alasio.deploy_dev.compile.errors import CompileError
from alasio.deploy_dev.compile.requirement import compile_environment, requirements_of_pyproject
from alasio.deploy_dev.compile.resolver import Resolver
from alasio.deploy_dev.compile.writer import requirements_text
from alasio.deploy_dev.metadata import MetadataProvider
from alasio.deploy_dev.pack_server.fetch_wheel import create_client
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.file.tomlfile import toml_loads
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_text, atomic_write
from alasio.logger import logger

__all__ = ['CompileError', 'CompileResult', 'RequirementsCompile']

# The index a project's dependencies are read from when it declares none: the
# official PyPI simple index, the value of RepoBase.DEFAULT_INDEX
DEFAULT_INDEX = 'https://pypi.org/simple'

# The dependency files of a project, relative to its root
PYPROJECT_FILE = 'pyproject.toml'
REQUIREMENTS_FILE = 'requirements.txt'


class CompileResult(Struct):
    """
    What a compile decided and what it did with it.

    Attributes:
        path (str): Path of the requirements file
        written (bool): True when the file changed, False when it already
            held the pins
        pins (Dict[str, str]): The pins, PEP 503 name -> version
        sources (Dict[str, str]): The index that serves each pin
        added (List[str]): The names that were not in the file
        removed (List[str]): The names the file had and the compile dropped
        changed (List[str]): The names whose version moved
    """
    path: str
    written: bool
    pins: Dict[str, str]
    sources: Dict[str, str]
    added: List[str]
    removed: List[str]
    changed: List[str]


class RequirementsCompile:
    """
    Compile the requirements of one project into its lock file.

    One instance is one run of the developer tools of a mod: compile() resolves
    the declaration and writes the lock, and the ``pack_deps`` property answers
    the manager of the pack channel of the same mod. The two share one metadata
    reader, so a compile followed by ``pack_deps`` in the same instance reads
    every index page and every METADATA once (see ``provider``).

    The instance is a context manager: the reader and the client of the run are
    closed when the block ends, so a caller never closes it by hand (``with``
    on an event loop is ``async with``).

    Attributes:
        root (PathStr): Root folder of the project
        client (httpx2.AsyncClient | AsyncHttpClient | None): Client the
            requests are sent with, None to create one for the run
        proxy (str): Proxy the requests of the run are routed through
        target (TargetPython): The interpreter the lock is compiled for
        resolution (str): 'highest' or 'lowest'
        header (str): Text written at the top of the lock file
    """

    def __init__(self, root='', client=None, proxy='', target=None, resolution='highest',
                 output='', header='', dest='', provider=None, entry=None):
        """
        Args:
            root (str): Root folder of the project. Defaults to '',
                env.PROJECT_ROOT
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client to
                send the requests with. Defaults to None, a client of the run
            proxy (str): Proxy url the requests are routed through, e.g.
                'http://127.0.0.1:7890'. Defaults to '', a direct connection
            target (TargetPython, optional): The interpreter the lock is
                compiled for. Defaults to None, the running interpreter
            resolution (str): 'highest' or 'lowest'. Defaults to 'highest'
            output (str): Path of the lock file, absolute or relative to the
                project root. Defaults to '', `<root>/requirements.txt`
            header (str): Text written at the top of the lock file, its lines
                starting with '#'. Defaults to '', no header
            dest (str): Folder the page fetcher may write into; the compiler
                reads every file in memory, so nothing is written. Defaults to
                '', `<root>/temp/compile`
            provider (MetadataProvider, optional): The metadata reader of a
                run that reads several files: the pages and the METADATA of
                the compile stay in its caches for the caller that reads them
                next. Defaults to None, a provider of this instance, closed by
                aclose()
            entry (ModEntry, optional): The declaration ``pack_deps``
                maintains. Defaults to None, the entry of the project root

        Raises:
            CompileError: If no project root is given or set
        """
        if not root:
            if not env.PROJECT_ROOT:
                raise CompileError(
                    'PROJECT_ROOT is not set, call env.set_project_root() first or pass root=')
            root = env.PROJECT_ROOT
        self.root = PathStr.new(root)
        self.client = client
        self.proxy = proxy
        self.target = target if target is not None else TargetPython()
        self.resolution = resolution
        self.header = header
        self._output = output
        self.dest = dest or str(self.root.joinpath('temp/compile'))
        self._given_provider = provider
        self._given_entry = entry
        # the client created by this instance, closed by aclose(); an injected
        # one belongs to the caller
        self._owned_client = None

    def __repr__(self):
        return f'{self.__class__.__name__}(root="{self.root}")'

    @cached_property
    def provider(self):
        """
        The metadata reader of the run, shared by the compile and the pack_deps.

        The provider lives as long as this instance: compile() reads the pages
        and the METADATA of the resolution into it, and ``pack_deps`` asks the
        same questions of the same versions, so it costs no request for them.

        Returns:
            MetadataProvider: The reader, the one given to the constructor when
                it was
        """
        if self._given_provider is not None:
            return self._given_provider
        client = self.client
        if client is None:
            client = self._owned_client = create_client(self.proxy)
        return MetadataProvider(client=client, dest=self.dest)

    @cached_property
    def pack_deps(self):
        """
        The manager of the pack channel of the same mod, over the same reader.

        The manager reads the requirements file this compile writes, so the
        pins it decides are the ones of the lock, and its index pages and
        METADATA are the ones the compile already read.

        Returns:
            PackDepsManager: The manager of the run
        """
        # imported here: a compile that maintains no pack channel does not pay
        # for the declaration model of a mod entry
        from alasio.config.entry.mod_entry import ModEntry
        from alasio.deploy_dev.compile.pack_deps.manager import PackDepsManager

        entry = self._given_entry
        if entry is None:
            entry = ModEntry(self.root)
        return PackDepsManager(
            entry=entry, client=self.client, proxy=self.proxy, provider=self.provider)

    def aclose(self):
        """
        Close the metadata reader and the client this instance owns.

        A provider or a client given to the constructor belongs to the caller
        and is left open, like every other client of the domain. Closing is
        idempotent, and a closed instance must not run another compile. The
        ``with`` block calls this, so a caller that uses ``with`` never has to;
        a caller that is already on an event loop awaits aclose_async(), or uses
        ``async with``.

        Raises:
            RuntimeError: If called on an event loop, where closing an async
                client needs an await the caller has to make
        """
        try:
            trio.lowlevel.current_task()
        except RuntimeError:
            # not on a loop: the close is a short run of its own
            trio.run(self.aclose_async)
            return
        raise RuntimeError(
            'RequirementsCompile.aclose() was called on an event loop: closing the async '
            'client needs an await, so use `async with` (or await aclose_async()) instead '
            'of `with`')

    async def aclose_async(self):
        """
        Close the metadata reader and the client this instance owns, see aclose().
        """
        if self._given_provider is None:
            # the reader of this instance, if the run ever asked for one
            provider = cached_property.pop(self, 'provider')
            if provider is not None:
                await provider.aclose()
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    def __enter__(self):
        """
        Run the tools of the mod as one block, see aclose().

        Returns:
            RequirementsCompile: This instance
        """
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Close the reader and the client of the run when the block ends.

        Args:
            exc_type (type | None): The exception of the block, None when none
            exc_value (BaseException | None): The exception of the block
            traceback (traceback | None): The traceback of the exception
        """
        self.aclose()

    async def __aenter__(self):
        """
        Run the tools of the mod as one block on an event loop, see aclose_async().

        Returns:
            RequirementsCompile: This instance
        """
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """
        Close the reader and the client of the run when the block ends, see __exit__().

        Args:
            exc_type (type | None): The exception of the block, None when none
            exc_value (BaseException | None): The exception of the block
            traceback (traceback | None): The traceback of the exception
        """
        await self.aclose_async()

    """
    The declaration of the project
    """

    @cached_property
    def path_pyproject(self):
        """
        Returns:
            PathStr: {root}/pyproject.toml
        """
        return self.root.joinpath(PYPROJECT_FILE)

    @cached_property
    def path_requirements(self):
        """
        Returns:
            PathStr: The lock file, {root}/requirements.txt by default
        """
        if not self._output:
            return self.root.joinpath(REQUIREMENTS_FILE)
        path = PathStr.new(self._output)
        if path.is_abspath():
            return path
        return self.root.joinpath(self._output)

    @cached_property
    def data(self):
        """
        The parsed pyproject.toml.

        Returns:
            dict: The document

        Raises:
            CompileError: If the file is missing or is not a TOML document
        """
        try:
            content = atomic_read_text(self.path_pyproject)
        except FileNotFoundError:
            raise CompileError(
                f'The project has no dependency file: "{self.path_pyproject}"') from None
        try:
            return toml_loads(content)
        except Exception as e:
            raise CompileError(
                f'Invalid TOML in "{self.path_pyproject}": {e}') from None

    @cached_property
    def name(self):
        """
        The PEP 503 name of the project, the annotation of a direct pin.

        Returns:
            str: The name, e.g. 'azurlaneautoscript'

        Raises:
            CompileError: If the document declares no project name
        """
        project = self.data.get('project')
        name = project.get('name') if isinstance(project, dict) else None
        if not name:
            tool = self.data.get('tool')
            poetry = tool.get('poetry') if isinstance(tool, dict) else None
            name = poetry.get('name') if isinstance(poetry, dict) else None
        if not name:
            raise CompileError(
                f'"{self.path_pyproject}" declares no project name, in [project].name '
                f'or [tool.poetry].name')
        return canonicalize_name(name)

    @cached_property
    def indexes(self):
        """
        The indexes of the compile, the primary one first.

        The official PyPI index is the primary one unless the document declares
        an index with ``default = true``, which replaces it. Every
        ``[[tool.uv.index]]`` is read afterwards, in the declared order.

        Returns:
            Tuple[Index, ...]: The indexes

        Raises:
            CompileError: If an index declares no url, or two indexes share a
                name
        """
        primary = Index.of(DEFAULT_INDEX, primary=True)
        others = []
        names = {}
        for entry in self._uv_indexes():
            if not isinstance(entry, dict):
                raise CompileError(
                    f'[[tool.uv.index]] must be a table, got {type(entry).__name__}')
            url = entry.get('url')
            if not isinstance(url, str) or not url:
                raise CompileError('[[tool.uv.index]] declares no url')
            index = Index.of(url, primary=bool(entry.get('default', False)))
            name = entry.get('name')
            if isinstance(name, str) and name:
                names[name] = index
            if index.primary:
                primary = index
            else:
                others.append(index)
        self._index_names = names
        return tuple([primary] + others)

    def _uv_indexes(self):
        """
        The ``[[tool.uv.index]]`` entries of the document.

        Returns:
            list: The entries, an empty list when the document declares none

        Raises:
            CompileError: If the table is not a list of tables
        """
        tool = self.data.get('tool')
        uv = tool.get('uv') if isinstance(tool, dict) else None
        entries = uv.get('index') if isinstance(uv, dict) else None
        if entries is None:
            return []
        if not isinstance(entries, list):
            raise CompileError('[[tool.uv.index]] must be a list of tables')
        return entries

    @cached_property
    def sources(self):
        """
        The index a distribution is read from, when the document names one.

        A ``[tool.uv.sources]`` entry of the form ``{ index = "name" }`` names
        an index of ``[[tool.uv.index]]``. The other forms of the table (a git
        repository, a path, a url) are sources this compiler cannot read: it
        resolves versions of an index, so they are refused.

        Returns:
            dict[str, str]: PEP 503 name -> base url of the index

        Raises:
            CompileError: If an entry names a source this compiler cannot read,
                or an index the document does not declare
        """
        tool = self.data.get('tool')
        uv = tool.get('uv') if isinstance(tool, dict) else None
        table = uv.get('sources') if isinstance(uv, dict) else None
        if not isinstance(table, dict):
            return {}
        # the named indexes are collected while the list of indexes is built
        _ = self.indexes
        out = {}
        for name, entry in table.items():
            if isinstance(entry, list):
                entry = entry[0] if entry else {}
            if not isinstance(entry, dict) or 'index' not in entry:
                kind = ', '.join(sorted(entry)) if isinstance(entry, dict) else type(entry).__name__
                raise CompileError(
                    f'[tool.uv.sources] of "{name}" declares a source this compiler does '
                    f'not read ({kind}): only {{ index = "..." }} is supported')
            index_name = entry['index']
            index = self._index_names.get(index_name)
            if index is None:
                raise CompileError(
                    f'[tool.uv.sources] of "{name}" names the index "{index_name}", which '
                    f'the document does not declare in [[tool.uv.index]]')
            out[canonicalize_name(name)] = index.url
        return out

    """
    The compile
    """

    def compile(self, upgrade=False, extras=(), all_extras=False, groups=()):
        """
        Resolve the requirements and write the lock file.

        Args:
            upgrade (bool): True to resolve every distribution again, False to
                prefer the versions the file already pins. Defaults to False
            extras (Iterable[str]): Optional dependency groups to include.
                Defaults to (), the base dependencies only
            all_extras (bool): True to include every optional dependency
                group. Defaults to False
            groups (Iterable[str]): Dependency groups to include. Defaults to
                (), none of them

        Returns:
            CompileResult: The pins and what the write changed

        Raises:
            CompileError: If the requirements cannot be resolved, or the file
                cannot be read back
        """
        return trio.run(functools.partial(
            self.compile_async, upgrade=upgrade, extras=extras, all_extras=all_extras,
            groups=groups))

    async def compile_async(self, upgrade=False, extras=(), all_extras=False, groups=()):
        """
        Resolve the requirements and write the lock file, see compile().

        Args:
            upgrade (bool): True to resolve every distribution again
            extras (Iterable[str]): Optional dependency groups to include
            all_extras (bool): True to include every optional dependency group
            groups (Iterable[str]): Dependency groups to include

        Returns:
            CompileResult: The pins and what the write changed
        """
        roots = requirements_of_pyproject(
            self.data, extras=extras, all_extras=all_extras, groups=groups,
            environment=compile_environment(self.target))
        if not roots:
            raise CompileError(
                f'"{self.path_pyproject}" declares no dependency, there is nothing to compile')
        logger.info(
            f'Compiling "{self.name}" for python {self.target.py_version} '
            f'({len(roots)} requirement(s), resolution={self.resolution})')

        existing = {} if upgrade else self._existing_pins()
        # the reader of the instance, not of the call: the pages and the METADATA
        # it holds answer the pack_deps of the same run
        resolver = Resolver(
            self.provider, self.indexes, self.target, existing=existing,
            resolution=self.resolution, sources=self.sources)
        resolution = await resolver.resolve(roots)
        text = requirements_text(
            self.name, resolution, self.indexes, header=self.header)
        self._verify(text, resolution)

        old = self._existing_pins()
        pins = resolution.pins
        added = sorted(name for name in pins if name not in old)
        removed = sorted(name for name in old if name not in pins)
        changed = sorted(name for name in pins if name in old and old[name] != pins[name])
        written = self._write(text)
        logger.info(
            f'Compiled {len(pins)} pin(s): {len(added)} added, {len(changed)} changed, '
            f'{len(removed)} removed, {len({url for url in resolution.sources.values()})} '
            f'index(es)')
        if added:
            logger.info(f'Added: {", ".join(added)}')
        if changed:
            logger.info(
                f'Changed: {", ".join(f"{name} {old[name]} -> {pins[name]}" for name in changed)}')
        if removed:
            logger.info(f'Removed: {", ".join(removed)}')
        return CompileResult(
            path=str(self.path_requirements), written=written, pins=dict(pins),
            sources=dict(resolution.sources), added=added, removed=removed, changed=changed)

    def _existing_pins(self):
        """
        The pins of the lock file as it is on disk.

        Returns:
            dict[str, str]: PEP 503 name -> version, empty when the file does
                not exist or cannot be read as a requirements file

        Raises:
            CompileError: If the file exists and the client parser rejects it
        """
        try:
            content = atomic_read_text(self.path_requirements)
        except FileNotFoundError:
            return {}
        return Requirements(content).dict_deps

    def _verify(self, text, resolution):
        """
        Read the lock back with the parser of the client installer.

        A lock the installer cannot read is a broken release, so the compile
        fails here rather than on a user machine. The pins read back are
        compared too: the writer and the reader of a lock are two pieces of
        code, and a file that spells a pin differently is a bug of the writer.

        Args:
            text (str): Content of the lock
            resolution (Resolution): The pins it was written from

        Raises:
            CompileError: If the file cannot be read back, or its pins are not
                the pins of the resolution
        """
        try:
            pins = Requirements(text).dict_deps
        except ValueError as e:
            raise CompileError(
                f'The lock the compiler wrote cannot be read back: {e}') from None
        if pins != resolution.pins:
            missing = sorted(set(resolution.pins) - set(pins))
            extra = sorted(set(pins) - set(resolution.pins))
            wrong = sorted(
                name for name in pins
                if name in resolution.pins and pins[name] != resolution.pins[name])
            raise CompileError(
                f'The lock the compiler wrote does not read back as the resolution: '
                f'missing={missing}, extra={extra}, '
                f'wrong={[(name, pins[name], resolution.pins[name]) for name in wrong]}')

    def _write(self, text):
        """
        Write the lock, unless the file already holds the content.

        Args:
            text (str): Content of the lock

        Returns:
            bool: True when the file was written
        """
        try:
            current = atomic_read_text(self.path_requirements)
        except FileNotFoundError:
            current = None
        if current == text:
            logger.info(f'The lock already holds the answer: "{self.path_requirements}"')
            return False
        atomic_write(self.path_requirements, text)
        logger.info(f'Write file {self.path_requirements}')
        return True
