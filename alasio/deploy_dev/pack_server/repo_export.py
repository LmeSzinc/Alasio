"""
Export the release trees of a run: .repo and .dep.

A release folder is a copy of the workspace, not a mirror: the working tree of
the mod as a client runs it, with the files a pack channel would otherwise
download on the first start. The run writes the two trees into the workspace:

    {workspace}/.repo    the working tree of the latest version, with
                         config/deploy.yaml (the copy of the template of the
                         version), resources/app.asar (the archive of the
                         client the library channel carries) and the pack area:
                         index.pack, the history of the version and the
                         dependency snapshot
    {workspace}/.dep     the install tree of every dependency pack channel,
                         the content of a site-packages folder, with the ledger
                         of every distribution (.pack/{dist}/index.pack)

An operator copies them into the folders of a release:

    .repo/*  ->  {release}/
    .dep/*   ->  {release}/toolkit/Lib/site-packages/

and the release starts without downloading a pack: the ledgers name the
version latest.pack serves, so the client validates the files it already has
(ResetJob) instead of rebuilding the tree of the server.

The files are written by the unpack job of the client, not by a writer of this
module: UnpackJob is the flow a client runs, so an export yields the tree a
client would have built, and a run over a tree that is already current writes
nothing (every record is checked against the file on the disk before it is
written). The one part of the job that is skipped is the copy of the pack into
the job file, see ReleaseExportJob.

The tree is decoded from the encoder of this run when the run wrote the full
pack (PackFullView): the records and their encoded data are still in the
memory, so the pack of the version is not read back from the disk. A pack that
was kept from an earlier run was not encoded by this run and is read once
(PackDecodeBase).

The export is a step of the generation (repo.Repo.run), not of the
publication: a tree that cannot be exported fails the run, latest.pack keeps
naming the previous version, and nothing of the run is uploaded.

Every file the export writes is touched (RepoBase.touch), the set the cleanup
of the workspace keeps and the local manifest of the run records. The touched
files are the records of the version and the ledgers, not a walk of the
exported folder: a file an earlier export left that the version does not have
is removed by the cleanup of the run.

Usage:
    from alasio.deploy_dev.pack_server.repo_export import ReleaseExport

    export = ReleaseExport(base, pack, dep)
    export.run()
"""

from hashlib import sha256

import trio

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import DeployTarget
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.pack_model import FileInfo
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy_dev.pack_server.pack_check import PackChecksum
from alasio.deploy_dev.pack_server.repo_dep import FULL_PACK_FILE
from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.ext.path.atomic import CHUNK_SIZE, atomic_copy, atomic_failure_cleanup, atomic_open, atomic_read_bytes
from alasio.logger import logger

# folder of the working tree of the release, the subfolder of the workspace. The
# leading '.' marks a private folder: the workspace mirrors to the server as it
# is, and a path segment that starts with '.' is not published
REPO_DIR = '.repo'

# folder of the site-packages tree of the release, the subfolder of the workspace
DEP_DIR = '.dep'

# the template of the deployment config, relative to the root of the working
# tree: a file of the version, copied to DEPLOY_CONFIG by the export. The webapp
# locates the project root by it and offers the first time setup page when only
# the template is there, so a release always ships both
DEPLOY_TEMPLATE = 'config/deploy.template.yaml'

# the deployment config of the release, relative to the root of the working
# tree: the copy of DEPLOY_CONFIG of the version. It is not in the version
# itself (config/*.yaml is ignored by the git repo), and a release does not
# start without it: the electron client refuses a config without
# PythonExecutable, and the backend creates the file on the first start
# otherwise
DEPLOY_CONFIG = 'config/deploy.yaml'

# the electron archive of the library, relative to the site-packages tree of the
# export (.dep): the layout is the one the wheel build writes
# (alasio/deploy_dev/wheel/alasio_wheel.py) and the shell update stages into a
# packaged client (alasio/backend/app/restart/shell_update.py)
BUNDLED_APP_ASAR = 'alasio/deploy_data/webapp/app.asar'

# the electron archive of the release, relative to the root of the working tree:
# the client loads the app from it, and it sits next to the executable of a
# release. The export copies the archive of the library here, see
# ReleaseExport._run()
APP_ASAR = 'resources/app.asar'


def _file_sha256(file, chunk_size=CHUNK_SIZE):
    """
    sha256 of a file, read in chunks so a large file costs no large buffer.

    Args:
        file (str): File to hash
        chunk_size (int): Bytes read per chunk. Defaults to CHUNK_SIZE

    Returns:
        bytes: The 32 bytes digest

    Raises:
        FileNotFoundError: If the file does not exist
        OSError: If the file cannot be read
    """
    digest = sha256()
    with atomic_open(file, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                return digest.digest()
            digest.update(chunk)


class ReleaseExportError(RuntimeError):
    """
    Raised when a release tree is not exported.

    The export is a step of the generation: the caller records the run as
    failed, latest.pack keeps naming the previous version, and nothing of the
    run is uploaded.
    """


class ReleaseExportJob(UnpackJob):
    """
    An unpack job of a release tree: the pack is not copied to the job file.

    UnpackJob.write() stores the whole pack to {ledger}/workspace/job.pack
    before it unpacks, a copy of the pack the job is about to read: the flow it
    serves has the pack in the memory of a download, and the copy is the resume
    base of an interrupted run. Neither applies to an export: the pack of the
    tree is packrepo/{version}/full.pack on the disk of this run, and a run of
    the export rebuilds the tree from it (the tmp files of an interrupted run
    are reused by content, see TmpManager).
    """

    def write(self):
        """
        Skip the job file: the pack of the export is already on the disk.
        """

    @cached_property
    def old_fileinfo(self):
        """
        The records of the previous export, the deletion base of the run.

        A target without a ledger is the normal case of the first export: the
        folder is created by this job, and every file of it that the version
        does not have is a leftover the cleanup of the run removes
        (RepoBase.cleanup). The client flow reports a target without a ledger as
        a warning, an export has nothing to report.

        Returns:
            dict[str, IdxInfo]: Records of the local ledger, {} when the tree
                was never exported
        """
        index = self.local_index
        if index.exists:
            return index.fileinfo()
        return {}


class PackFullView:
    """
    The decoder-shaped view over the encoder of a full pack of this run.

    The members are the ones UnpackJob reads of a PackDecodeBase: fileinfo,
    extract_index_pack() and catfile(). The records are the FileInfo objects of
    PackFull.fileinfo, which carry the encoded data of their content
    (FileInfo.data): a record is decoded from the memory, not from the pack
    file.

    A C (copied) record carries no data of its own: it references the record it
    duplicates (source_lookback), whose content is identical. catfile()
    resolves that record and decodes its data with the line ending of the copy.

    data is empty: the serialized pack is never assembled in the memory, only
    the records are. The job of an export never reads it, see
    ReleaseExportJob.
    """

    def __init__(self, pack):
        """
        Args:
            pack (PackFull): The encoder of a pack this run wrote, its fileinfo
                is the record set of the version
        """
        self.pack = pack
        # list of the records in the encoded order and {path: index} of them,
        # the source of a copied record is a 1-based lookback into the list.
        # None until the first copied record needs them
        self._ordered = None
        self._index_of = None

    @property
    def current_version(self):
        """
        Returns:
            str: Version the pack packs, e.g. the commit sha1
        """
        return self.pack.current_version

    @property
    def data(self):
        """
        Returns:
            bytes: Empty, the pack is not assembled in the memory
        """
        return b''

    @property
    def fileinfo(self):
        """
        Returns:
            dict[str, FileInfo]: The records of the version in the encoded order
        """
        return self.pack.fileinfo

    def extract_index_pack(self):
        """
        Returns:
            bytes: The index pack of the version, header and index section
        """
        return self.pack.index_pack

    def catfile(self, info):
        """
        The working tree content of a record.

        Args:
            info (FileInfo): Record of the version

        Returns:
            memoryview: Content of the file, the checkout line ending applied

        Raises:
            PackDecodeError: If the content does not match the record
        """
        source = self._source_of(info)
        if source is info:
            return memoryview(PackDecodeBase.decode_content(info, info.data))
        # the content is verified with the encoding of the source record and
        # written with the line ending of the copy: the two records share their
        # content, not their eol
        probe = FileInfo(
            path=info.path, size=source.size, sha1=source.sha1,
            algo=source.algo, data_size=source.data_size, eol=info.eol,
        )
        return memoryview(PackDecodeBase.decode_content(probe, source.data))

    def _source_of(self, info):
        """
        The record a record without data of its own takes its content from.

        Args:
            info (FileInfo): Record of the version

        Returns:
            FileInfo: The record itself when it carries data, otherwise the
                record its source_lookback chain ends at
        """
        if info.data or not info.source_lookback:
            return info
        if self._ordered is None:
            self._ordered = list(self.pack.fileinfo.values())
            self._index_of = {record.path: i for i, record in enumerate(self._ordered)}
        index = self._index_of[info.path]
        while not info.data and info.source_lookback:
            index -= info.source_lookback
            info = self._ordered[index]
        return info


class ReleaseExport:
    """
    Export the two trees of a release, see the module docstring.

    Usage:
        export = ReleaseExport(base, pack, dep)
        export.run()
    """

    def __init__(self, base, pack, dep):
        """
        Args:
            base (RepoBase): Workspace and declarations of the repo
            pack (RepoPack): The repo tree packs of the run after its run()
            dep (RepoDep): The dependency packs of the run after its run(), its
                published snapshots are the versions of the .dep tree
        """
        self.base = base
        self.pack = pack
        self.dep = dep

    @cached_property
    def repo_root(self):
        """
        Folder of the working tree of the release: {workspace}/.repo

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.base.workspace.joinpath(REPO_DIR)

    @cached_property
    def dep_root(self):
        """
        Folder of the site-packages tree of the release: {workspace}/.dep

        Returns:
            PathStr: Absolute path of the folder
        """
        return self.base.workspace.joinpath(DEP_DIR)

    def run(self):
        """
        Export the two trees of the release.

        The version is the one latest.pack of this run carries, read from the
        encoder and the kept checksum of RepoPack, never from the file on the
        disk: latest.pack is written after this step, and a client of the
        previous version must not read an index of this one.

        Raises:
            ReleaseExportError: If the packs of the repo tree were not
                generated, a tree cannot be exported, or a check of the result
                fails
            OSError: If a file cannot be read or written
        """
        pack = self.pack.pack
        if pack is None:
            raise ReleaseExportError(
                'The packs of the repo tree were not generated, see RepoPack.run()')
        # the identity latest.pack is built from: the version and the checksum
        # a client compares its local ledger against
        info = LatestInfo.parse(pack.latest_pack(self.pack.kept_checksum))
        logger.info(
            f'Exporting the release trees of "{self.base.workspace.name}": '
            f'version={info.version}')
        # the tmp files of an interrupted write of a previous run are ours
        atomic_failure_cleanup(self.repo_root, recursive=True)
        atomic_failure_cleanup(self.dep_root, recursive=True)
        trio.run(self._run, info)

    async def _run(self, info):
        """
        Unpack the two trees and copy the files a release needs besides them.

        Neither file is a record of the version, so both are copied after the
        trees are unpacked: the deploy config from the template of the version,
        and the client archive from the library the .dep tree now carries.

        Args:
            info (LatestInfo): Version and index pack checksum of the run
        """
        await self._export_repo(info)
        await self._export_dep()
        # The template is a file of the version, not a file generated from a
        # model of this library: the mod owns its content and may declare keys
        # this library does not know yet. The release ships the copy so a first
        # start finds a deploy config, and the template stays next to it, the
        # file the webapp locates the project root by and offers the first time
        # setup page from
        self._lazy_copy_file(
            self.repo_root.joinpath(DEPLOY_TEMPLATE), self.repo_root.joinpath(DEPLOY_CONFIG),
            missing=f'The version has no {DEPLOY_TEMPLATE}, a release needs it to '
                    f'locate the project and to write {DEPLOY_CONFIG}',
            what='deploy config')
        # The archive is the client the mod runs on: a release folder loads it
        # from resources/ next to its executable, and a tree copied over such a
        # folder replaces the archive with this one. The source is the copy the
        # .dep tree carries, the same file the shell update stages into a
        # packaged client, never a file of the machine of the run
        self._lazy_copy_file(
            self.dep_root.joinpath(BUNDLED_APP_ASAR), self.repo_root.joinpath(APP_ASAR),
            missing='The dependency packs of the run carry no app.asar: the library '
                    'is not a pack dep of this mod, or its wheel was built without one',
            what='app asar')

    async def _export_repo(self, info):
        """
        Unpack the working tree of the version and touch every file of it.

        Args:
            info (LatestInfo): Version and index pack checksum of the run

        Raises:
            ReleaseExportError: If the tree cannot be exported or its ledger is
                not the one of the version
        """
        decoder = self._repo_decoder()
        logger.info(f'Unpacking the tree of {info.version} to "{self.repo_root}"')
        job = ReleaseExportJob(decoder, root=self.repo_root, name='')
        if not await job.run():
            raise ReleaseExportError(
                f'Failed to export the tree to "{self.repo_root}", '
                f'the workspace is kept for the next run')
        self._touch_target(self.repo_root, '', decoder)
        self._check_ledger(self.repo_root, '', info.version, bytes.fromhex(info.checksum))

    async def _export_dep(self):
        """
        Unpack the install tree of every dependency pack channel.

        The versions are the published snapshots of RepoDep, the entries the
        tree pack carries as .pack/packdep.pack: the export of a channel is the
        one a client reads from the tree, whatever the dependency file of the
        repo pins.

        Raises:
            ReleaseExportError: If a full pack does not match the snapshot, a
                tree cannot be exported, or its ledger is not the one of the
                version
            OSError: If a pack cannot be read
        """
        published = self.dep.published
        total = len(published)
        if not total:
            logger.info(
                f'No dependency to export for "{self.base.workspace.name}", skipped')
            return
        logger.info(
            f'Unpacking {total} dependencies of "{self.base.workspace.name}" '
            f'to "{self.dep_root}"')
        for index, (name, (version, checksum)) in enumerate(published.items(), start=1):
            file = self.base.packdep_folder.joinpath(name).joinpath(version).joinpath(FULL_PACK_FILE)
            # the snapshot is the source of the tree: a full pack that is not
            # the one the snapshot names is a broken run, and the export must
            # not bake it into a release
            actual = PackChecksum.from_file(file).index_checksum
            if actual != checksum:
                raise ReleaseExportError(
                    f'The full pack of "{name}=={version}" does not match the '
                    f'dependency snapshot of the run: "{file}"')
            decoder = PackDecodeBase(atomic_read_bytes(file))
            decoder.validate()
            logger.info(
                f'[{index}/{total}] Unpacking "{name}=={version}" to "{self.dep_root}"')
            job = ReleaseExportJob(decoder, root=self.dep_root, name=name)
            if not await job.run():
                raise ReleaseExportError(
                    f'Failed to export the dependency "{name}=={version}" to '
                    f'"{self.dep_root}", the workspace is kept for the next run')
            self._touch_target(self.dep_root, name, decoder)
            self._check_ledger(self.dep_root, name, version, checksum)

    def _repo_decoder(self):
        """
        The decoder of the full pack of the version.

        A pack this run wrote is decoded from the encoder, which still holds
        every record and its encoded data (see PackFullView); a kept pack was
        not encoded by this run and is read once.

        Returns:
            PackFullView | PackDecodeBase: The decoder of the version

        Raises:
            OSError: If a kept pack cannot be read
        """
        pack = self.pack.pack
        if pack is not None and InstanceCacheOperation.has(pack, 'fileinfo'):
            return PackFullView(pack)
        return PackDecodeBase(atomic_read_bytes(self.pack.full_pack_file))

    def _lazy_copy_file(self, source, file, missing, what):
        """
        Copy a file to a path of the exported tree, unless it is already there.

        The copy is skipped when the target holds the content of the source, so
        a second export of the same version writes nothing: both files are
        hashed (sha256, read in chunks) and the copy runs only when the digests
        differ.

        The target is touched either way: it is a file of the run, the cleanup
        of the workspace keeps it and the local manifest records it, whether
        this run wrote it or found it current.

        Args:
            source (str): File to copy
            file (str): Target path of the tree
            missing (str): Head of the error raised when the source is not there
            what (str): Name of the file in the log lines, e.g. 'app asar'

        Raises:
            ReleaseExportError: If the source does not exist
            OSError: If a file cannot be read, copied or written
        """
        try:
            digest = _file_sha256(source)
        except FileNotFoundError:
            raise ReleaseExportError(f'{missing}: "{source}"') from None
        try:
            current = _file_sha256(file)
        except FileNotFoundError:
            current = None
        if current == digest:
            logger.info(f'{what.capitalize()} exists, skipped: "{file}"')
        else:
            logger.info(f'Copy {what}: "{source}" -> "{file}"')
            atomic_copy(source, file)
        self.base.touch(file)

    def _check_ledger(self, root, name, version, checksum):
        """
        Check that a tree carries the ledger of the version it exports.

        The ledger is the file the client reads of a target: its version tells
        an update is not needed, and its checksum is what the client compares
        with latest.pack (the tree) or with the dependency snapshot (a
        distribution). The path is the one DeployTarget gives, the path the
        client's own job resolves, so a tree copied into a release is read
        where the export wrote it.

        Args:
            root (str): Root of the exported tree
            name (str): Ledger key of the target, '' for the tree
            version (str): Version the ledger must name
            checksum (bytes): Index pack checksum the ledger must carry

        Raises:
            ReleaseExportError: If the ledger is missing, of another version or
                of another checksum
            OSError: If the ledger cannot be read
        """
        file = DeployTarget(root=root, name=name).index_file
        try:
            ledger = PackChecksum.from_file(file)
        except (OSError, ValueError) as e:
            raise ReleaseExportError(
                f'Failed to read the ledger of the exported tree: "{file}": {e}') from e
        if ledger.current_version != version or ledger.index_checksum != checksum:
            raise ReleaseExportError(
                f'The ledger of the exported tree is not the one of the version: '
                f'"{file}", version={ledger.current_version}, '
                f'checksum={ledger.index_checksum.hex()}, '
                f'expected version={version}, checksum={checksum.hex()}')

    def _touch_target(self, root, name, decoder):
        """
        Touch the files of an exported tree: the records of the version and its
        ledger.

        The touched set is the exact set the export writes, never a walk of the
        folder (RepoBase.touch): the cleanup of the workspace keeps what is
        touched and removes everything else, so a file an earlier export left
        and the version does not have is removed by the cleanup of this run
        instead of being kept forever.

        Args:
            root (str): Root of the exported tree
            name (str): Ledger key of the target, '' for the tree
            decoder (PackFullView | PackDecodeBase): The decoder the tree was
                exported with, its records are the paths of the files
        """
        files = 0
        for path, info in decoder.fileinfo.items():
            if info.edit == 2:
                continue
            # the decoder is localized by the job: the paths are the local
            # paths of the target, the ones the job wrote
            self.base.touch(root.joinpath(path))
            files += 1
        self.base.touch(DeployTarget(root=root, name=name).index_file)
        logger.info(
            f'Touched {files + 1} release files of "{root}"')
