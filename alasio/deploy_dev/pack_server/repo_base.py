"""
The state every step of a repo of the pack server shares.

A pack config generates one workspace, the folder of the repo in the pack
folder of the run directory:

    {run directory}/pack/{Author}_{Repo}_{Branch}/

The generation is the flow of PackRepoModel, one module per step:

- repo_base (this module): the workspace and the declarations of the mod entry
  the steps read, nothing is written here;
- repo_dep: steps 4.1 and 6, the pure python wheel of every version the
  dependency packs are built of, downloaded to `.wheel_cache/{name}/{version}/`,
  and the packs of the dependencies to `packdep/{name}/{version}/`;
- repo_wheel: steps 4.2 and 5, every wheel of the pins the repo provides,
  downloaded to the same cache and synced to `wheel/{name}/{version}/`, the
  folder a client installs from;
- repo_pypi: the pages of the pip index of that folder: the simple page of
  every name, the find-links page of the folder and the flat page. The pages
  are written with latest.pack, never before it: the page of a distribution
  lists the wheels of the version latest.pack names;
- repo_pack: step 2, the full pack and the update packs of the repo tree to
  `packrepo/{commit}/`;
- repo_latest: step 7, `packrepo/latest.pack`, the version a client reads;
- repo: the flow itself, the steps in the order they depend on each other.

The steps tell the base which files they use (touch), and the
base removes every other file of the workspace after the version is published
(cleanup): a step does not carry a cleanup of its own.

The steps do not clone the repo and do not open it: the git repo of the
config is an input, opened by the caller (PackRepo clones and fetches it),
like the git flow of the packs. Nothing here writes to the repo and nothing
writes to the workspace: the steps of the flow do.

The mod entry of the latest commit declares what is generated, see
ModEntry: the dependency file of the repo (requirements_file), the names
that update by pack (pack_deps) and the indexes the repo provides
(provide_wheel). The declarations are read from the entry file
of the commit, never from the config of the run directory: the config is the
same for every version of the repo, the entry is the version.

The wheels are fetched from the index every pin of the dependency file
annotates ('# from <index>', the annotation uv writes with
--emit-index-annotation); a pin without an annotation is fetched from
DEFAULT_INDEX, the official PyPI index. The pypi_mirrors of the entry is not
used here: it is the source a client installs its dependencies from, and it
may point at the pack server of this very repo, so the pack server would
fetch the wheels from its own output.

Usage:
    from alasio.deploy_dev.pack_server.repo_base import RepoBase

    base = RepoBase(repo, config.data)
    base.deps
"""

import os
from contextlib import asynccontextmanager
from typing import Dict

import msgspec

from alasio.config.entry.mod_entry import MOD_ENTRY_PATH
from alasio.deploy.simple_pip.cleanup_folder import CleanupByPreserved
from alasio.deploy.simple_pip.parse_req import DepInfo, normalize_index_url
from alasio.deploy_dev.pack_server.fetch_wheel import create_client
from alasio.deploy_dev.pack_server.lookback_wheel import LookbackWheel
from alasio.deploy_dev.pack_server.manifest import split_paths
from alasio.deploy_dev.pack_server.parse_dep import normalize_name
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import file_remove, folder_rmtree_empty
from alasio.ext.path.calc import subpath_to, to_posix
from alasio.ext.path.iter import iter_entry
from alasio.ext.path.validate import validate_filename
from alasio.logger import logger

# folder of the repos in the run directory, see PackRepoModel
PACK_FOLDER = 'pack'

# folder of the packs of the repo tree, the subfolder of the workspace
PACKREPO_FOLDER = 'packrepo'

# folder of the packs of the dependencies, the subfolder of the workspace
PACKDEP_FOLDER = 'packdep'

# folder of the wheels a client installs with pip, the subfolder of the workspace
WHEEL_FOLDER = 'wheel'

# folder of the downloaded wheels, the input of the pack build, the subfolder of
# the workspace. The leading '.' marks a private folder: the workspace mirrors to
# the server as it is, and a path segment that starts with '.' is not published
WHEEL_CACHE_FOLDER = '.wheel_cache'

# the index a pin without a source is fetched from: the official PyPI simple
# index. The parser reads the official index as no source too (a pin resolved
# from PyPI needs no index of its own), so both cases meet here
DEFAULT_INDEX = 'https://pypi.org/simple'


class DepBuildInfo(msgspec.Struct):
    """
    One dependency of the window, and what the repo asks to build of it.

    A dependency is in the mapping of RepoBase.deps once, with its versions and
    its objectives on one object: a dependency is a pack channel or a set of
    provided wheels, never both, see RepoBase.deps.

    Attributes:
        name (str): PEP 503 normalized distribution name
        latest (DepInfo): The pin of the latest commit, the version a client
            updates to
        lookbacks (Dict[str, DepInfo]): {version: pin} of the older versions of
            the window, the versions a client may still be on, the old side of
            the update packs
        pack_dep (bool): True when the name is one of the pack_deps of
            the mod entry: the dependency packs are built of it
        provide_wheel (bool): True when the latest pin comes from an index the
            mod entry provides and the name is not a pack dep: every wheel of
            the version is published to the pip index
    """

    name: str
    latest: DepInfo
    lookbacks: Dict[str, DepInfo] = msgspec.field(default_factory=dict)
    pack_dep: bool = False
    provide_wheel: bool = False

    @property
    def versions(self):
        """
        The versions of the dependency in the window, the latest first.

        Returns:
            list[DepInfo]: The pin of the latest commit, then the pins of the
                lookback commits in the order they were read
        """
        return [self.latest, *self.lookbacks.values()]


def is_provided_source(source, provide_mirrors):
    """
    Whether a pin source is one of the indexes the mod entry provides.

    The compare is on the normalized url (see normalize_index_url): the
    dependency file and the entry write the same index differently, e.g. a
    trailing slash or the case of the host.

    Args:
        source (str): Normalized base url of the index of a pin, '' when the
            pin has no source
        provide_mirrors (dict[str, str]): The provided indexes of the entry,
            see RepoBase.provide_wheel

    Returns:
        bool: True when the pin comes from a provided index, the ones the
            update pack provides to the client
    """
    if not source:
        return False
    return normalize_index_url(source) in provide_mirrors


class RepoBase:
    """
    The workspace of one repo and the declarations its steps read.

    The instance is the input of every step of the flow: the folders of the
    workspace, the window of the lookback config, and what the mod entry of
    the latest commit declares. The dependency files of the repo are sampled
    once on the instance (LookbackWheel): build a new instance for every run,
    the next run must see the new commits.

    Usage:
        base = RepoBase(repo, config.data)
        base.deps
    """

    def __init__(self, repo, config, client=None):
        """
        Args:
            repo (GitRepo | MockGitRepo): Git repo of the config, opened by
                the caller (PackRepo clones and fetches it); every revision of
                the mod entry and of a dependency file is read from it
            config (PackRepoModel): Config of the repo, read from a
                PackRepoConfig: the Lookback group is the window to sample.
                The dependency file, the pack names and the provided indexes
                are declared by the mod entry of the latest commit
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client
                the wheel fetches of the flow are sent with, e.g. the injected
                client of a test. Its lifetime belongs to the caller. Defaults
                to None, the steps create the client of their own, through the
                proxy of the config Env when it is set (see fetch_client)

        Raises:
            ValueError: If the config makes a folder name that is not a
                single safe path component (see workspace)
        """
        self.repo = repo
        self.config = config
        self.client = client
        # the pins of the dependency files over the lookback window, the
        # latest commit first, see LookbackWheel
        self.wheel = LookbackWheel(repo, config)
        # the files this run uses, relative to the workspace: the steps touch
        # what they read or write, see cleanup() and repo_manifest()
        self._touched = set()
        # the folder name is checked before anything is written, see workspace
        _ = self.workspace

    @cached_property
    def workspace(self):
        """
        Folder of the repo in the pack folder: pack/{Author}_{Repo}_{Branch}

        Every step of the flow writes under this folder, and the publication
        mirrors it to the server, so the name must be a single path component:
        a Branch like 'feature/x' would nest the folder of this repo inside the
        folder of another config, and the cleanup of the stale folders of that
        config could remove the packs of this one. See validate_filename.

        Returns:
            PathStr: Absolute path of the folder
        """
        name = f'{self.config.Repo.Author}_{self.config.Repo.Repo}_{self.config.Repo.Branch}'
        validate_filename(name)
        return env.PROJECT_ROOT.joinpath(PACK_FOLDER).joinpath(name)

    @cached_property
    def packrepo_folder(self):
        """
        Folder of the packs of the repo tree: {workspace}/packrepo

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.workspace.joinpath(PACKREPO_FOLDER)

    @cached_property
    def packdep_folder(self):
        """
        Folder of the packs of the dependencies: {workspace}/packdep

        One folder per dependency, see repo_dep for the layout.

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.workspace.joinpath(PACKDEP_FOLDER)

    @cached_property
    def wheel_folder(self):
        """
        Folder of the wheels a client installs with pip: {workspace}/wheel

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.workspace.joinpath(WHEEL_FOLDER)

    @cached_property
    def wheel_cache_folder(self):
        """
        Folder of the downloaded wheels: {workspace}/.wheel_cache

        The private cache and the input of the pack build, see repo_dep and
        repo_wheel: the leading '.' marks the folder as private, the workspace
        mirrors to the server as it is and a path segment that starts with '.'
        is not published. The dot also hides the folder in the default view of
        a file manager on linux, the cache is not a file of the run an operator
        reads.

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.workspace.joinpath(WHEEL_CACHE_FOLDER)

    def wheel_cache_of(self, name, version):
        """
        Folder of one distribution version in the private cache.

        The two steps that fetch a wheel write here, see repo_dep and
        repo_wheel: the folder of a version holds its index page and the wheels
        of the run, whichever step asked for them.

        Args:
            name (str): PEP 503 normalized distribution name
            version (str): Exact version of the distribution

        Returns:
            PathStr: {workspace}/.wheel_cache/{name}/{version}
        """
        return self.wheel_cache_folder.joinpath(name).joinpath(version)

    # ------------------------------------------------------------------
    # The http client of the fetches
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def fetch_client(self):
        """
        The http client the wheel fetches of one step are sent with.

        The index pages and the wheels are downloaded from the index a pin
        declares (PyPI, py38deps, ...), never from a mirror of the client: the
        mirror is the output of the pack server, so the server cannot install
        its own input from it. The machine reaches those indexes itself, or
        through Env.Proxy when the local network cannot, see PackRepoModel.Env.

        One client serves every fetcher of the step, with or without the proxy:
        the versions of the window and the distributions of the step reuse the
        connections of that client instead of opening a connection pool each,
        and it is closed when the step ends. An injected client takes
        precedence and is used as it is: its lifetime and its route belong to
        the caller.

        A client is per step because a step is one event loop (the two steps
        that fetch run through trio.run, see RepoWheel and RepoDep): the pool
        and the keep-alive connections of a client belong to the loop they were
        used on, and the loop of a step is gone when the step returns.

        Yields:
            httpx2.AsyncClient | AsyncHttpClient: The client to send the fetches
                with, the injected one or the one of the step
        """
        if self.client is not None:
            yield self.client
            return
        proxy = self.config.Env.Proxy
        if proxy:
            logger.info(f'Downloading the wheels through the proxy "{proxy}"')
        client = create_client(proxy)
        try:
            yield client
        finally:
            await client.aclose()

    # ------------------------------------------------------------------
    # The files of the run
    # ------------------------------------------------------------------

    def touch(self, file):
        """
        Record a file this run uses, so the cleanup keeps it.

        Every step of the flow touches what it reads or writes: a pack it
        built or kept, a wheel of the cache, an index page, latest.pack. The
        cleanup removes every file of the workspace that was not touched, so a
        step that forgets a file loses it on the next publication.

        Args:
            file (str): Absolute path of the file, below the workspace or
                below a folder it names, e.g. the cache folder of a version
        """
        # the paths of the flow are normalized ('/'), subpath_to() answers the
        # part below the workspace and None for a path that is not below it
        path = to_posix(str(file))
        relative = subpath_to(path, to_posix(str(self.workspace)))
        if relative is None:
            # a file outside the workspace is not one this run may keep: the
            # record would never match a file of the tree, the cleanup would
            # remove the file the step meant to keep
            logger.warning(
                f'Touched a file outside the workspace of "{self.workspace.name}": "{path}"')
            relative = path
        self._touched.add(relative)

    def repo_manifest(self):
        """
        The entries of the run, split into the published and the private ones.

        The set touch() fills is the whole truth of a run: every file a step
        read or wrote. This accessor hands it out in the form the manifest of
        the workspace records (see manifest.split_paths): the tmp files of an
        interrupted run and the manifest itself are not entries of it, and a
        path with a '.' or '_' segment is private.

        The split is computed here and nowhere else: the manifest of the
        workspace records it, and the cleanup of the run keeps the whole set the
        two halves come from, plus the manifest file itself. No other module
        classifies a path of the workspace.

        Returns:
            tuple[list[str], list[str]]: (files, private), '/'-separated paths
                relative to the workspace, both sorted. The files are the ones
                a mirror publishes, the private ones stay on the disk of the
                generator, e.g. the wheel cache
        """
        return split_paths(self._touched)

    def cleanup(self):
        """
        Remove every file and folder of the workspace this run did not touch.

        The set of touched files is the whole truth of a run: a version that
        slid out of the lookback window, a wheel of a pin that left the
        provided set, the folder of a pack renamed away, the page of a version
        no step reads anymore. A folder that holds no touched file is removed
        as a whole, without walking it, see CleanupByPreserved: the parts of
        the cache and of the published folders the run did not use cost one
        removal and no per file IO.

        The removal runs once, after the version is published, so a failed run
        never deletes a file a client still needs. The workspace is the only
        tree of the run the flow writes: the log folder lives in the run
        directory, next to the config, and is never part of it. A removal that
        fails is logged and retried by the next run.

        Returns:
            int: Number of files and folders removed
        """
        cleaner = CleanupByPreserved()
        # the tree first, then what the run preserves, see CleanupByPreserved:
        # a preserved path drops the registered entries of its own name
        files, folders = _read_tree(self.workspace)
        cleaner.register_file(files)
        cleaner.register_folder(folders)
        cleaner.register_preserved(
            {self.workspace.joinpath(path) for path in self._touched})
        removed = 0
        # the answer is the deepest first, a file before its folder: the
        # removal runs in its order, a folder that still holds a file is kept
        for path, is_dir in cleaner.get_cleanup_entries():
            if is_dir:
                logger.info(f'Removing stale folder: "{path}"')
                if not folder_rmtree_empty(path):
                    logger.warning(f'Failed to remove stale folder "{path}"')
                    continue
            else:
                logger.info(f'Removing stale file: "{path}"')
                if not file_remove(path):
                    logger.warning(f'Failed to remove stale file "{path}"')
                    continue
            removed += 1
        logger.info(
            f'Cleaned up "{self.workspace.name}": '
            f'{len(self._touched)} files kept, {removed} removed')
        return removed

    @cached_property
    def latest_commit(self):
        """
        The latest commit of the repo: the head of the branch to pack.

        Returns:
            str: Commit sha1

        Raises:
            ValueError: If the repo has no branch to pack, see
                PackRepoLookback.latest_commit
        """
        return self.wheel.lookback.latest_commit

    @cached_property
    def lookback_commit(self):
        """
        The lookback commits of the window, newest first.

        Returns:
            list[str]: Commit sha1s, the latest commit is not in the list

        Raises:
            ValueError: If the repo has no branch to pack, see
                PackRepoLookback.lookback_commit
        """
        return self.wheel.lookback.lookback_commit

    @cached_property
    def entry(self):
        """
        The mod entry of the latest commit of the repo.

        The latest commit is the version a client updates to: its entry is the
        one the updated client reads before it reconciles its dependencies, so
        the declarations the pack server follows are the ones of that commit.
        The entry of the commit is parsed once by LookbackWheel and shared
        with the sample of the window.

        Returns:
            ModEntry: The declarations of the entry

        Raises:
            ValueError: If the repo has no branch to pack, or the entry cannot
                be parsed (see LookbackWheel)
        """
        return self.wheel.repo_entry[self.latest_commit]

    @cached_property
    def provide_wheel(self):
        """
        The indexes the mod entry provides with the update pack, normalized.

        The provide_wheel of the mod entry at the latest commit
        of the repo: the wheels of the dependencies whose dependency file
        annotates one of these indexes are the ones the repo provides, the
        ones a client cannot install from the index it installs the other
        dependencies from, see deps. The keys are normalized with
        normalize_index_url, the rule the parser of a dependency file applies
        to its '# from' annotations, so the two sides agree on what one index
        is.

        Returns:
            dict[str, str]: Normalized index base url -> url as the entry
                declares it

        Raises:
            ValueError: If the repo has no branch to pack, the entry cannot
                be read, or an index of it is not an http(s) url (see
                ModEntry)
        """
        out = {}
        commit = self.latest_commit
        for url in self.entry.provide_wheel:
            normalized = normalize_index_url(url)
            if not normalized:
                # the model refuses a url that is not http(s), this is the
                # guard of a value that reached it another way (an entry built
                # in code, never read from a file)
                logger.warning(
                    f'Provided index of {MOD_ENTRY_PATH} at commit {commit} '
                    f'is not an http(s) url, skipped: {url!r}')
                continue
            out.setdefault(normalized, url)
        logger.debug(
            f'Using provide_wheel of {MOD_ENTRY_PATH} at commit '
            f'{commit}: {", ".join(out)}')
        return out

    @cached_property
    def deps(self):
        """
        The dependencies to build, and what each of them asks for.

        The names of the mod entry pack_deps and the names whose latest
        pin comes from an index the entry provides, see DepBuildInfo: the first
        ones get the dependency packs, the second ones get their wheels
        published to the pip index. The two objectives never apply to the same
        name: a name that updates by pack is already served by its pack
        channel, publishing its wheels too would be the same version served
        twice, so a pack dep is never provided, whatever index its pin comes
        from. A name is in the mapping once, with the versions of the window
        and the objectives on one object.

        A name the latest commit does not pin is not in the mapping: it has no
        version a client updates to, so nothing is built of it this run.

        Returns:
            dict[str, DepBuildInfo]: {PEP 503 name: what to build of it}, in
                the order the pins of the latest commit were read

        Raises:
            ValueError: If the repo has no branch to pack, the mod entry
                cannot be read, or a dependency file of the window cannot be
                read (see LookbackWheel)
        """
        declared = self.entry.pack_deps
        commit = self.latest_commit
        if declared:
            logger.info(
                f'Using pack_deps of {MOD_ENTRY_PATH} at {commit}: {", ".join(declared)}')
        else:
            logger.info(
                f'No pack_deps declared by {MOD_ENTRY_PATH} at {commit}, '
                f'no dependency pack is built')
        # the names are matched PEP 503 normalized: the entry may write a name
        # the dependency file writes another way. A dict keeps the order of the
        # declaration and answers 'in' in constant time
        pack_names = dict.fromkeys(normalize_name(name) for name in declared)

        # the window is sampled here: it reads the entries and the dependency
        # files of the commits, the same lookup the entry above comes from
        latest_deps = self.wheel.latest_deps
        lookback_deps = self.wheel.lookback_deps
        provide_mirrors = self.provide_wheel

        deps = {}
        for name, pin in latest_deps.items():
            pack_dep = name in pack_names
            # a name of both objectives is served by its pack channel alone:
            # the wheels of the same version are not published a second time
            provide_wheel = not pack_dep and is_provided_source(pin.source, provide_mirrors)
            if not (pack_dep or provide_wheel):
                # neither a pack channel nor a provided wheel of this repo
                continue
            deps[name] = DepBuildInfo(
                name=name, latest=pin, pack_dep=pack_dep, provide_wheel=provide_wheel)

        # the historical versions are the old side of the update packs: only a
        # pack channel reads them, a provided pin is installed at the version
        # of the latest commit
        for (name, version), pin in lookback_deps.items():
            info = deps.get(name)
            if info is not None:
                info.lookbacks[version] = pin

        # the declared names nothing is built of, reported once per name
        lookback_names = {name for name, _ in lookback_deps}
        for name in pack_names:
            if name in deps:
                continue
            if name in lookback_names:
                logger.warning(
                    f'Dependency "{name}" is not pinned by the latest commit, '
                    f'no pack is built of it, its historical packs are kept')
            else:
                logger.warning(
                    f'Dependency "{name}" is not pinned to a version in the lookback window, '
                    f'nothing is built of it; a dependency of the mod entry pack_deps '
                    f'must be pinned with "==" in the dependency file of the repo')
        return deps


def _read_tree(root):
    """
    Read the paths of the files and of the folders of a tree, in one walk.

    The paths are absolute and '/'-separated, the form CleanupByPreserved
    registers and answers. The walk is iter_entry(), one os.scandir per folder
    with the DirEntry objects it already carries, so the kind of an entry costs
    no call of its own.

    Args:
        root (str): Root folder of the tree

    Returns:
        tuple[list[str], list[str]]: Absolute paths of the files and of the
            folders
    """
    files = []
    folders = []
    for entry in iter_entry(root, recursive=True):
        path = entry.path.replace(os.sep, '/')
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            folders.append(path)
        else:
            files.append(path)
    return files, folders
