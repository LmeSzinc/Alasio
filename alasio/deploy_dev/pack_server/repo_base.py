"""
The state every step of a repo of the pack server shares.

A pack config generates one workspace, the folder of the repo in the pack
folder of the run directory:

    {run directory}/pack/{Author}_{Repo}_{Branch}/

The generation is the flow of PackRepoModel, one module per step:

- repo_base (this module): the workspace and the declarations of the mod entry
  the steps read, nothing is written here;
- repo_dep: steps 4.1 and 6, the pure python wheel of every version the
  dependency packs are built of, downloaded to `_wheel_cache/{name}/{version}/`,
  and the packs of the dependencies to `packdep/{name}/{version}/`;
- repo_wheel: steps 4.2 and 5, every wheel of the pins the repo provides,
  downloaded to the same cache and synced to `wheel/{name}/{version}/`, the
  folder a client installs from;
- repo_pypi: the pip index pages of that folder (not written yet);
- repo_pack: step 2, the full pack and the update packs of the repo tree to
  `packrepo/{commit}/`;
- repo_latest: step 7, `packrepo/latest.pack`, the version a client reads;
- repo: the flow itself, the steps in the order they depend on each other.

The steps do not clone the repo and do not open it: the git repo of the
config is an input, opened by the caller (PackRepo clones and fetches it),
like the git flow of the packs. Nothing here writes to the repo and nothing
writes to the workspace: the steps of the flow do.

The mod entry of the latest commit declares what is generated, see
PackRepoEntry: the dependency file of the repo (requirements_file), the names
that update by pack (update_pack_deps) and the indexes the repo provides
(update_provide_pypi_mirrors). The declarations are read from the source of
the entry, never from the config of the run directory: the config is the same
for every version of the repo, the entry is the version.

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

from typing import Dict

import msgspec

from alasio.deploy.simple_pip.parse_req import DepInfo, normalize_index_url
from alasio.deploy_dev.pack_server.lookback_wheel import LookbackWheel
from alasio.deploy_dev.pack_server.parse_dep import normalize_name
from alasio.deploy_dev.pack_server.parse_entry import ENTRY_FILE
from alasio.ext import env
from alasio.ext.cache import cached_property
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
# the workspace. The leading '_' marks a private folder: a publication skips it
WHEEL_CACHE_FOLDER = '_wheel_cache'

# the index a pin without a source is fetched from: the official PyPI simple
# index. The parser reads the official index as no source too (a pin resolved
# from PyPI needs no index of its own), so both cases meet here
DEFAULT_INDEX = 'https://pypi.org/simple'


class DepBuildInfo(msgspec.Struct):
    """
    One dependency of the window, and what the repo asks to build of it.

    A dependency is in the mapping of RepoBase.deps once, with its versions and
    its objectives on one object: a dependency may be a pack channel, a set of
    provided wheels, or both.

    Attributes:
        name (str): PEP 503 normalized distribution name
        latest (DepInfo): The pin of the latest commit, the version a client
            updates to
        lookbacks (Dict[str, DepInfo]): {version: pin} of the older versions of
            the window, the versions a client may still be on, the old side of
            the update packs
        pack_dep (bool): True when the name is one of the update_pack_deps of
            the mod entry: the dependency packs are built of it
        provide_wheel (bool): True when the latest pin comes from an index the
            mod entry provides: every wheel of the version is published to the
            pip index
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
            see RepoBase.update_provide_pypi_mirrors

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
                to None, every fetch creates and closes the client of its own

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
        Folder of the downloaded wheels: {workspace}/_wheel_cache

        The private cache and the input of the pack build, see repo_dep and
        repo_wheel: the leading '_' marks the folder as private, a publication
        skips it.

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
            PathStr: {workspace}/_wheel_cache/{name}/{version}
        """
        return self.wheel_cache_folder.joinpath(name).joinpath(version)

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
            PackRepoEntry: The declarations of the entry

        Raises:
            ValueError: If the repo has no branch to pack, or the entry cannot
                be parsed (see LookbackWheel)
        """
        return self.wheel.repo_entry[self.latest_commit]

    @cached_property
    def update_provide_pypi_mirrors(self):
        """
        The indexes the mod entry provides with the update pack, normalized.

        The update_provide_pypi_mirrors of the mod entry at the latest commit
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
                PackRepoEntry)
        """
        out = {}
        commit = self.latest_commit
        for url in self.entry.update_provide_pypi_mirrors:
            normalized = normalize_index_url(url)
            if not normalized:
                # the entry parser refuses a url that is not http(s), this is
                # the guard of a value that reached the model another way
                logger.warning(
                    f'Provided index of {ENTRY_FILE} at commit {commit} '
                    f'is not an http(s) url, skipped: {url!r}')
                continue
            out.setdefault(normalized, url)
        logger.debug(
            f'Using update_provide_pypi_mirrors of {ENTRY_FILE} at commit '
            f'{commit}: {", ".join(out)}')
        return out

    @cached_property
    def deps(self):
        """
        The dependencies to build, and what each of them asks for.

        The names of the mod entry update_pack_deps and the names whose latest
        pin comes from an index the entry provides, see DepBuildInfo: the first
        ones get the dependency packs, the second ones get their wheels
        published to the pip index, a name of both gets both. A name is in the
        mapping once, with the versions of the window and the two objectives
        on one object.

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
        declared = self.entry.update_pack_deps
        commit = self.latest_commit
        if declared:
            logger.info(
                f'Using update_pack_deps of {ENTRY_FILE} at {commit}: {", ".join(declared)}')
        else:
            logger.info(
                f'No update_pack_deps declared by {ENTRY_FILE} at {commit}, '
                f'no dependency pack is built')
        # the names are matched PEP 503 normalized: the entry may write a name
        # the dependency file writes another way. A dict keeps the order of the
        # declaration and answers 'in' in constant time
        pack_names = dict.fromkeys(normalize_name(name) for name in declared)

        # the window is sampled here: it reads the entries and the dependency
        # files of the commits, the same lookup the entry above comes from
        latest_deps = self.wheel.latest_deps
        lookback_deps = self.wheel.lookback_deps
        provide_mirrors = self.update_provide_pypi_mirrors

        deps = {}
        for name, pin in latest_deps.items():
            pack_dep = name in pack_names
            provide_wheel = is_provided_source(pin.source, provide_mirrors)
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
                    f'nothing is built of it; a dependency of the mod entry update_pack_deps '
                    f'must be pinned with "==" in the dependency file of the repo')
        return deps
