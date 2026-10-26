"""
Generate the packs of the python dependencies of a repo for the pack server.

A pack config with PythonDeps builds the packs of the python distributions the
repository depends on. The versions to build are the versions the repository
asked for in its dependency files, the wheels of those versions are the input
of the build, and every dependency gets a channel of its own:

1. fetch the wheel of every version of every dependency from the PyPI simple
   index (PEP 503) of the config to
   pack/{Author}_{Repo}_{Branch}/wheel/{name}/{version}/{filename}.whl, see
   WheelFetcher. Every wheel is required, the target and the historical
   versions alike: a run that cannot fetch one of them fails, no repo pack is
   published, so a published channel never lacks an update side
2. build the full pack of the target version and the update pack from every
   other version to it, encoded from the wheels by PackWheel /
   PackWheelUpdate (a pack of a wheel is the install tree of the wheel, see
   pack_wheel)
3. commit(), after the repo tree is published: remove the wheel folders the
   lookback window does not ask for, and the dependency folders, the pack
   folders and the update pack files the window does not ask for

The previous channel keeps serving the clients until commit(): a failed run
or a failed repo publication leaves every old pack in place.

The channel of a dependency is the layout the client reads (ServerFile):

    pack/{Author}_{Repo}_{Branch}/packdep/{name}/{new}/full.pack
    pack/{Author}_{Repo}_{Branch}/packdep/{name}/{new}/update_{old}.pack

Only the target version has a full pack, the rule the git flow keeps for the
latest commit: a client downloads the full pack of the version it updates to
once and updates with update packs afterwards, so a version of the window
only needs an update path to the target.

pack/{Author}_{Repo}_{Branch} is the folder of the packs of the config (see
PackRepoModel), the packdep folder lives next to the packrepo folder of the
git flow, so the channel URL is
{BaseUrl}/{Author}_{Repo}_{Branch}/packdep/{name} and the wheels are served
under .../wheel/ with no change of the client.

The versions of a dependency are the pins of PythonDeps.RequirementFiles over
the lookback window, sampled by LookbackWheel: the pin of the latest commit is
the target of the dependency (the version a client updates to, the only
version with a full pack), the pins of the old commits are the versions a
client may still be on, every one of them gets an update path to the target (a
full pack is only built of the target). The names to build are the
update_pack_deps of the mod entry at the latest commit, read from its source
by PackRepoEntry: a name the entry does not declare is not fetched and gets no
pack, and a declared name that no commit of the window pins yields no wheel
and no pack and is reported with a warning (a range constraint builds nothing,
the version must be pinned with '=='). A name the latest commit does not pin
has no target: nothing is built of it this run and its historical packs are
kept.

The bytes of a pack only depend on the wheel of its version, so the pack of an
earlier run is kept as it is: the identity of the file is read back
(kept_pack) and a file of another format, of another version pair, or a file
that cannot be read as a pack is written again. A run over a channel that is
up to date builds nothing; commit() still removes the entries the window does
not ask for.

The run leaves the snapshots of the channels it generated on the DepGen
instance (the published attribute): the version and index checksum of every
dependency whose latest commit pin has a target. The snapshots are built from
the target full pack itself, never from a per dependency latest.pack. Every
wheel is mandatory, the target and the historical versions alike: a
dependency that cannot be fetched fails the run, so a published repo tree
never references a dependency whose channel is incomplete.

The pack encoder receives the DepGen object and encodes the snapshots into
.pack/packdep.pack when the version extras are built.

The repo of a config is an input of the generator, not cloned here: the caller
opens the git repo of the config and passes it in, the generation only reads
it, like PackRepoGen.

Usage:
    from alasio.deploy_dev.pack_server.dep_gen import DepGen
    from alasio.deploy_dev.pack_server.model import PackRepoConfig
    from alasio.git.repo import GitRepo

    config = PackRepoConfig('LmeSzinc_Alasio_master.yaml')
    repo = GitRepo(repo_path).read_lazy()
    dep = DepGen(repo, config.data)
    dep.run()
    # publish the repo tree, then remove the old dependency entries
    dep.commit()
"""

from msgspec import Struct

from alasio.deploy.pack.packdep import validate_packdep_name
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.pack.pack_wheel_update import PackWheelUpdate
from alasio.deploy_dev.pack_server.fetch_wheel import WHEEL_FOLDER, WheelFetcher, WheelNotFoundError
from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.deploy_dev.pack_server.lookback_wheel import LookbackWheel
from alasio.deploy_dev.pack_server.pack_check import PackChecksum, kept_pack
from alasio.deploy_dev.pack_server.parse_dep import normalize_name
from alasio.deploy_dev.pack_server.parse_entry import ENTRY_FILE, PackRepoEntry
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import (
    atomic_failure_cleanup, atomic_remove, atomic_rmtree, atomic_write_stream, folder_rmtree_empty
)
from alasio.ext.path.validate import validate_filename
from alasio.logger import logger

# folder of the repos in the run directory, see PackRepoModel
PACK_FOLDER = 'pack'

# folder of the dependency packs of a repo, the subfolder of a repo folder
PACKDEP_FOLDER = 'packdep'

# file of the full pack of a version, in the folder of the version
FULL_PACK_FILE = 'full.pack'

# mirror of the dependencies no PythonDeps.PypiMirror entry lists: the base
# url of the official PyPI, the default of PythonMirrorInfo.Url
DEFAULT_PYPI_MIRROR = 'https://pypi.org/simple'


class DepVersion(Struct):
    """
    One version of a dependency to build, and where the repo asks for it.

    Attributes:
        name (str): PEP 503 normalized distribution name
        version (str): Version the repository pins
        commit (str): Commit of the repository that asks for the version, the
            newest one when several commits ask for it
        target (bool): True for the version of the latest commit, the version
            a client updates to
    """

    name: str
    version: str
    commit: str
    target: bool


class DepGen:
    """
    Generate the dependency packs of one repo, see the module docstring.

    The dependency files of the repo are sampled once on the instance: build
    a new instance for every run, the next run must see the new commits.

    Usage:
        gen = DepGen(repo, config)
        gen.run()
    """

    def __init__(self, repo, config, client=None):
        """
        Args:
            repo (GitRepo | MockGitRepo): Git repo to sample, every revision
                of a dependency file is read from it
            config (PackRepoModel): Config of the repo, read from a
                PackRepoConfig: the PythonDeps group holds the files to
                sample and the mirror to fetch from
            client (httpx2.Client, optional): Client the wheel fetches are
                sent with, e.g. the injected client of a test. Its lifetime
                belongs to the caller. Defaults to None, every fetch creates
                and closes the client of its own

        Raises:
            ValueError: If the config makes a folder name that is not a
                single safe path component (see repo_folder)
        """
        self.repo = repo
        self.config = config
        self.client = client
        # {name: (version, index checksum)} of the channels generated by
        # run(), filled while the target full packs are written/kept. It is
        # read from the full packs, never from a per dependency latest.pack.
        self.published = {}
        # whether run() completed the generation of this instance, the
        # precondition of commit(): a failed run must not clean a channel
        self._generated = False
        # the pins of the dependency files over the lookback window, the
        # latest commit first, see LookbackWheel
        self.wheel = LookbackWheel(repo, config)
        # the update_pack_deps of the mod entry at the latest commit, read
        # from its source when the sample needs it
        self.entry = PackRepoEntry(repo)
        # the folder name is checked before anything is written, see repo_folder
        _ = self.repo_folder

    @cached_property
    def repo_folder(self):
        """
        Folder of the repo in the pack folder: pack/{Author}_{Repo}_{Branch}

        The folder the git flow of the config writes its packs to as well
        (see PackRepoGen.repo_folder): the packdep folder of the dependencies
        lives next to the packrepo folder of the project tree.

        The name must be a single path component: a Branch like 'feature/x'
        would nest the folder of this repo inside the folder of another
        config, and the cleanup of the stale folders of that config could
        remove the packs of this one. See validate_filename.

        Returns:
            PathStr: Absolute path of the folder
        """
        name = f'{self.config.Repo.Author}_{self.config.Repo.Repo}_{self.config.Repo.Branch}'
        validate_filename(name)
        return env.PROJECT_ROOT.joinpath(PACK_FOLDER).joinpath(name)

    @cached_property
    def wheel_folder(self):
        """
        Folder of the fetched wheels: {repo folder}/wheel

        The layout is the one of WheelFetcher:
        {wheel folder}/{name}/{version}/index_data.json and the wheel file.

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.repo_folder.joinpath(WHEEL_FOLDER)

    @cached_property
    def packdep_folder(self):
        """
        Folder of the dependency channels: {repo folder}/packdep

        One folder per dependency, see the module docstring for the layout.

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.repo_folder.joinpath(PACKDEP_FOLDER)

    @cached_property
    def managed(self):
        """
        The dependency names the mod entry declares for pack updates.

        The names come from the update_pack_deps of the entry at the latest
        commit of the repo, the version a client updates to: the client reads
        the entry of the updated tree before it reconciles its dependencies.
        The names are PEP 503 normalized and deduplicated, keeping the order
        of the declaration.

        Returns:
            dict[str, None]: PEP 503 normalized names

        Raises:
            ValueError: If the repo has no branch to pack, the entry cannot
                be read, or its update_pack_deps is written in a form the
                parser does not support, see PackRepoEntry
        """
        commit = self.wheel.lookback.latest_commit
        names = self.entry.update_pack_deps(commit)
        if not names:
            logger.info(
                f'No update_pack_deps declared by {ENTRY_FILE} at {commit}, '
                f'no dependency pack is built')
            return {}
        logger.info(
            f'Using update_pack_deps of {ENTRY_FILE} at {commit}: {", ".join(names)}')
        return dict.fromkeys(normalize_name(name) for name in names)

    @cached_property
    def sample(self):
        """
        The dependencies to build: the pins of the window, managed names only.

        Only the names of the mod entry update_pack_deps are kept (PEP 503
        normalized): a dependency the entry does not declare is not fetched
        and gets no pack. The pins of the latest commit come first (the
        target of an update), then the pins of the lookback commits, in the
        order the sampler answered; a dependency is in the mapping once, with
        its versions as one list.

        Returns:
            dict[str, list[DepVersion]]: {PEP 503 name: versions to build},
                the target of the name first when the name has one

        Raises:
            ValueError: If the repo has no branch to pack, a dependency file
                of the window cannot be read (see LookbackWheel), or the mod
                entry cannot be read (see managed)
        """
        # a config without a dependency file has nothing to sample, the
        # entry does not need to be read
        if not self.wheel.files:
            return {}

        # the window is sampled first: it reads the repo index the entry
        # lookup needs, see PackRepoLookback
        latest = self.wheel.latest_deps
        lookback = self.wheel.lookback_deps
        managed = self.managed

        sample = {}
        for (name, version), commit in latest.items():
            if name in managed:
                sample.setdefault(name, []).append(
                    DepVersion(name=name, version=version, commit=commit, target=True))
        for (name, version), commit in lookback.items():
            if name in managed:
                sample.setdefault(name, []).append(
                    DepVersion(name=name, version=version, commit=commit, target=False))
        for name in managed:
            if name not in sample:
                logger.warning(
                    f'Dependency "{name}" is not pinned to a version in the lookback window, '
                    f'nothing is built of it; a dependency of the mod entry update_pack_deps '
                    f'must be pinned with "==" in a dependency file of the config')
        return sample

    def run(self):
        """
        Generate the packs of the dependencies of the repo.

        The steps are the ones of the module docstring: fetch the wheels and
        build the full packs and the update packs. Nothing is deleted here:
        the previous channel keeps serving the clients until commit() is
        called after the repo tree is published.

        A run with no sampled dependency writes nothing and removes nothing.

        Raises:
            RunDirError: If env.PROJECT_ROOT is not a run directory of the
                pack server, see check_run_dir
            ValueError: If the repo has no branch to pack, a dependency file
                of the config cannot be read, or a wheel is not a pack target
                (see PackWheel)
            WheelNotFoundError: If a wheel of a dependency cannot be fetched,
                the target and the historical versions alike: every update
                path of the published channel must be complete
            httpx2.HTTPError: If a wheel cannot be fetched
            OSError: If a wheel or a pack cannot be written
        """
        # a run is a new publication set, also when the instance is reused
        self.published = {}
        self._generated = False
        check_run_dir()
        if not self.sample:
            self._generated = True
            logger.info(f'No dependency to build of "{self.repo_folder.name}", skipped')
            return

        # the names the latest commit still pins, the ones with a target: the
        # others have no version to update to, nothing is built of them and
        # their historical packs are kept
        targets = {}
        for name, deps in self.sample.items():
            if self._target_of(deps) is None:
                logger.warning(
                    f'Dependency "{name}" is not pinned by the latest commit, '
                    f'no pack is built of it, its historical packs are kept')
                continue
            targets[name] = deps

        versions = sum(len(deps) for deps in targets.values())
        logger.info(
            f'Generating the dependency packs of "{self.repo_folder.name}": '
            f'{len(targets)} dependencies, {versions} versions')
        # the tmp files of an interrupted run are ours to clean up, the real
        # files of the previous run keep serving the clients
        atomic_failure_cleanup(self.wheel_folder, recursive=True)
        atomic_failure_cleanup(self.packdep_folder, recursive=True)

        # 1. the wheel of every version of every dependency
        wheel_files = self._fetch_wheels(targets)
        # 2. the full packs, the update packs and the published snapshots
        self._write_packs(targets, wheel_files)
        # commit() may clean the channel only after every pack of the run is
        # on the disk
        self._generated = True
        logger.info(f'Dependency packs of "{self.repo_folder.name}" generated')

    def commit(self):
        """
        Remove the old wheels and packs after the repo tree is published.

        Called only after the repo latest.pack is written: the dependency
        channel of the previous repo version keeps its packs until the new
        repo tree is visible to the clients. A failed run or a failed repo
        publication leaves every old pack in place.

        Raises:
            RuntimeError: If run() was not called
        """
        if not self._generated:
            raise RuntimeError('DepGen.run() must be called before commit()')
        if not self.sample:
            return
        self._remove_stale_wheels(self.sample)
        self._remove_stale_packs(self.sample)
        logger.info(f'Dependency packs of "{self.repo_folder.name}" committed')

    def _fetch_wheels(self, targets):
        """
        Fetch the wheel of every version of every dependency.

        Every wheel is required, the target and the historical versions alike:
        a missing wheel fails the run instead of publishing a channel with a
        missing update pack. The client of the channel would otherwise have to
        rebuild from the target, and the channel would not be the complete
        publication the packdep snapshot describes.

        Args:
            targets (dict[str, list[DepVersion]]): Dependencies to fetch

        Returns:
            dict[tuple[str, str], PathStr]: {(name, version): wheel file} of
                the wheels that are available, a version whose wheel cannot
                be fetched has no entry

        Raises:
            httpx2.HTTPError: If a request fails: the retrying client of the
                fetcher absorbs a transport blip, an error that survives the
                attempts fails the run of the config
            OSError: If a wheel cannot be written
        """
        out = {}
        total = sum(len(deps) for deps in targets.values())
        index = 0
        for name, deps in targets.items():
            mirror = self._mirror_of(name)
            for dep in deps:
                index += 1
                logger.info(
                    f'[{index}/{total}] Fetching wheel of "{name}=={dep.version}", '
                    f'mirror="{mirror}", commit={dep.commit}')
                with WheelFetcher(
                        self.repo_folder, mirror, name, dep.version, client=self.client) as fetcher:
                    try:
                        out[(name, dep.version)] = fetcher.fetch()
                    except WheelNotFoundError as e:
                        role = 'target' if dep.target else 'historical'
                        raise WheelNotFoundError(
                            f'No usable wheel of the {role} dependency "{name}=={dep.version}", '
                            f'the dependency packs cannot be generated: {e}'
                        ) from e
        return out

    def _write_packs(self, targets, wheel_files):
        """
        Write the packs of every dependency.

        Args:
            targets (dict[str, list[DepVersion]]): Dependencies to build
            wheel_files (dict[tuple[str, str], PathStr]): Wheels fetched by
                this run, see _fetch_wheels

        Raises:
            ValueError: If a wheel is not a pack target, see PackWheel
            OSError: If a pack cannot be written
        """
        total = len(targets)
        for index, (name, deps) in enumerate(targets.items(), start=1):
            target = self._target_of(deps)
            logger.info(
                f'[{index}/{total}] Building dependency packs of "{name}": '
                f'{len(deps)} versions, target={target.version}')
            self._write_dep_packs(deps, target, wheel_files)

    def _write_dep_packs(self, deps, target, wheel_files):
        """
        Write the packs of one dependency, see the module docstring for the layout.

        The full pack of the target version an earlier run wrote is kept as it
        is, see kept_pack: only the packs that are missing are built, so a run
        over a channel that is up to date builds nothing.

        The per dependency latest.pack is not written: the repo packdep is the
        only publication the clients read; the legacy marker is left on the
        disk from an earlier run and is not updated.

        Args:
            deps (list[DepVersion]): Versions of the dependency, the target
                first
            target (DepVersion): Version of the latest commit, the version a
                client updates to, the only version with a full pack
            wheel_files (dict[tuple[str, str], PathStr]): Wheels of this run
        """
        name = target.name
        dep_folder = self.packdep_folder.joinpath(name)

        # 1. the full pack of the target version, the only full pack of the
        # channel: a client downloads it once and updates with update packs
        # afterwards, the versions of the window do not need one
        file = dep_folder.joinpath(target.version).joinpath(FULL_PACK_FILE)
        identity = kept_pack(file, PackWheel.PACK_VERSION, target.version, '')
        if identity is not None:
            logger.info(f'Full pack exists, skipped: "{file}"')
            target_pack = None
            target_checksum = identity.index_checksum
        else:
            wheel_file = wheel_files.get((name, target.version))
            if wheel_file is None:
                raise WheelNotFoundError(
                    f'No usable wheel of the target dependency "{name}=={target.version}", '
                    f'the full pack cannot be built')
            target_pack = PackWheel(wheel_file)
            logger.info(f'Writing full pack: "{file}"')
            atomic_write_stream(file, target_pack.iter_pack_data())
            # read the identity back from the file: the same read the kept
            # path does, so the published snapshot uses the checksum of the
            # pack that is on the disk
            target_checksum = PackChecksum.from_file(file).index_checksum

        # the snapshot of the channel, read from the target full pack: the
        # repo packdep is built from this, never from a per dependency
        # latest.pack
        try:
            validate_packdep_name(name)
            validate_filename(target.version)
        except ValueError as e:
            logger.warning(
                f'The channel of "{name}=={target.version}" has no packdep-safe name or '
                f'version, the dependency is left out of packdep: {e}')
            return
        self.published[name] = (target.version, target_checksum)

        # 2. the update pack from every other version to the target. The
        # install tree of the old version is the old side of the update pack
        # (the wheel is the input of the pack build, a version of the window
        # has no full pack of its own), the pack of the target the new side,
        # shared by every update pack of the dependency; the pack of a version
        # pair that is already on the disk is kept, see kept_pack
        new_pack = target_pack
        for dep in deps:
            if dep.target:
                continue
            file = dep_folder.joinpath(target.version).joinpath(f'update_{dep.version}.pack')
            identity = kept_pack(file, PackWheel.PACK_VERSION, target.version, dep.version)
            if identity is not None:
                logger.info(f'Update pack exists, skipped: "{file}"')
                continue
            old_wheel = wheel_files.get((name, dep.version))
            if old_wheel is None:
                raise WheelNotFoundError(
                    f'No usable wheel of the historical dependency "{name}=={dep.version}", '
                    f'the update pack cannot be built')
            if new_pack is None:
                new_wheel = wheel_files.get((name, target.version))
                if new_wheel is None:
                    # every update pack needs the new side: the target full
                    # pack of an earlier run is on the disk but its wheel is
                    # not, so the update packs cannot be completed
                    raise WheelNotFoundError(
                        f'No usable wheel of the target dependency "{name}=={target.version}", '
                        f'the update packs cannot be built')
                new_pack = PackWheel(new_wheel)
            old_pack = PackWheel(old_wheel)
            logger.info(f'Writing update pack: {dep.version} -> {target.version}, "{file}"')
            atomic_write_stream(file, PackWheelUpdate(new_pack, old_pack).iter_pack_data())

    def _remove_stale_wheels(self, sample):
        """
        Remove the wheel folders the sample does not ask for.

        The predicate is the name and the version of a folder: the wheel
        layout of WheelFetcher carries both, so a folder that is not a
        version of the sample is the leftover of an earlier run. A removal
        that fails (e.g. a file of the folder is held by another process) is
        logged and retried by the next run.

        Args:
            sample (dict[str, list[DepVersion]]): Dependencies the window asks
                for, the versions of a name without a target included: the
                historical releases of that name are kept
        """
        needed = {(name, dep.version) for name, deps in sample.items() for dep in deps}
        folder = self.wheel_folder
        for name in folder.iter_foldernames():
            name_folder = folder.joinpath(name)
            for version in name_folder.iter_foldernames():
                if (name, version) in needed:
                    continue
                version_folder = name_folder.joinpath(version)
                logger.info(f'Removing stale wheel folder: "{version_folder}"')
                try:
                    atomic_rmtree(version_folder)
                except OSError as e:
                    logger.warning(f'Failed to remove stale wheel folder "{version_folder}": {e}')
            # the name folder is left empty when every version of the
            # dependency was removed
            folder_rmtree_empty(name_folder)

    def _remove_stale_packs(self, sample):
        """
        Remove the dependency folders, pack folders and pack files the sample does not ask for.

        A dependency the sample does not ask for has no channel anymore, its
        folder (latest.pack included) is removed. The channel of a dependency
        is the folder of the target: it holds the full pack of the target and
        one update pack per other version of the window, so another version
        folder is the leftover of an earlier run and is removed (its full pack
        was the layout of an earlier run). An update pack of a version out of
        the window is removed too. Only the folders and the update_ files are
        recognized: a file of another tool in the folder is left alone. A
        removal that fails is logged and retried by the next run.

        A name the latest commit dropped has no target, nothing was published
        of it this run: its pack folders are kept as they are, only the
        versions out of the window are removed.

        Args:
            sample (dict[str, list[DepVersion]]): Dependencies the window asks
                for, the versions of a name without a target included: the
                historical releases of that name are kept
        """
        folder = self.packdep_folder
        for name in folder.iter_foldernames():
            dep_folder = folder.joinpath(name)
            deps = sample.get(name)
            if deps is None:
                logger.info(f'Removing stale dependency folder: "{dep_folder}"')
                try:
                    atomic_rmtree(dep_folder)
                except OSError as e:
                    logger.warning(
                        f'Failed to remove stale dependency folder "{dep_folder}": {e}')
                continue
            versions = {dep.version for dep in deps}
            target = self._target_of(deps)
            if target is None:
                keep = versions
                # the update packs the target folder must hold: one for every
                # other version of the window. A name the latest commit
                # dropped has no target and no update pack at all.
                updated = None
            else:
                keep = {target.version}
                updated = versions - {target.version}
            for version in dep_folder.iter_foldernames():
                version_folder = dep_folder.joinpath(version)
                if version not in keep:
                    logger.info(f'Removing stale pack folder: "{version_folder}"')
                    try:
                        atomic_rmtree(version_folder)
                    except OSError as e:
                        logger.warning(f'Failed to remove stale pack folder "{version_folder}": {e}')
                    continue
                if updated is None:
                    continue
                for filename in version_folder.iter_filenames(ext='.pack'):
                    if not filename.startswith('update_'):
                        continue
                    old = filename[len('update_'):-len('.pack')]
                    if old in updated:
                        continue
                    file = version_folder.joinpath(filename)
                    logger.info(f'Removing stale update pack: "{file}"')
                    try:
                        atomic_remove(file)
                    except OSError as e:
                        logger.warning(f'Failed to remove stale update pack "{file}": {e}')

    @staticmethod
    def _target_of(deps):
        """
        The target version of a dependency: the version of the latest commit.

        Args:
            deps (list[DepVersion]): Versions of one dependency

        Returns:
            DepVersion | None: The version of the latest commit, None when
                the latest commit does not pin the dependency
        """
        for dep in deps:
            if dep.target:
                return dep
        return None

    def _mirror_of(self, name):
        """
        The mirror to fetch a dependency from: PythonDeps.PypiMirror.

        A str mirror feeds every dependency; a list feeds the dependencies of
        each entry from its own Url, the first entry that lists the name
        wins. A name no entry lists is fetched from the official PyPI, the
        same default the model documents.

        Args:
            name (str): PEP 503 normalized distribution name

        Returns:
            str: Base url of a PyPI simple index (PEP 503)
        """
        mirror = self.config.PythonDeps.PypiMirror
        if isinstance(mirror, str):
            return mirror or DEFAULT_PYPI_MIRROR
        for entry in mirror:
            if any(normalize_name(dep) == name for dep in entry.Deps):
                return entry.Url or DEFAULT_PYPI_MIRROR
        return DEFAULT_PYPI_MIRROR
