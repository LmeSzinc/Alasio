"""
The files of a target that are not the working tree file of the same path.

A flow compares the records of a pack with the files of the target: it reads
a file to check a size and a sha1, to use it as the source of a patch, and to
decide which files of the old version are gone in the new one. For the vast
majority of targets the working tree is the whole answer, and this module
says nothing: LocalSideRepo answers every question with the empty value.

A dependency of a mod is the exception. As long as the mod does not declare
it in entry.pack_deps the distribution is installed by pip, and the deploy
format keeps no ledger of it: the files of the version are the ones the
installer wrote, listed in the RECORD of its .dist-info directory (PEP 376).
LocalSidePip reads that record and supplies the three facts a job can not get
from the tree:

    version   the version of the installation, '' when it can not name its
              files: a flow may not ask for an update pack it can not apply
    fileinfo  the records of the installed files, the deletion base of a
              target that has no ledger yet
    filedata  the content of the files an installer wrote differently from
              the pack of the same version

**A job never goes looking for an installation on its own.** A directory of
the distribution left behind in a site-packages folder describes no target of
this framework -- the folder is shared with every tool that ever wrote to it
-- so an installation is the old side of a target only when the caller says
so, see DeployJob(scene=...). The scene names are the ones of the mirror
scenes of the deploy framework: a mod tree is served from 'repo' channels, a
python package from 'pypi' ones, so a caller reads the same name in both
places.

Nothing here is trusted on its own. The RECORD is a description an installer
wrote, not the record of a pack flow: a claim of a pack about a file it lists
is advisory (see UpdateJob._read_file()), and the record that reads it checks
the content and downloads the record when it differs.
"""

from alasio.deploy.pack.pack_model import IdxInfo
from alasio.deploy.simple_pip.pip_list import DIST_INFO_SUFFIX, PipList, normalize_name
from alasio.deploy.simple_pip.whl_record import RecordManager
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.path.calc import subpath_to

# File of a .dist-info directory that lists the files of an installation,
# PEP 376
RECORD_FILE = 'RECORD'
# Suffix of the bytecode files python compiles from a distribution, the rows
# an installation adds to its RECORD and the pack of the same version, built
# from the tree of the distribution, does not
PYC_SUFFIX = '.pyc'
# First component of a RECORD path that leaves the site-packages folder, PEP
# 376: the files an installation writes outside of it, e.g. console scripts,
# are recorded as "../../Scripts/name"
OUTSIDE_COMPONENT = '..'


class LocalSide:
    """
    The files of a target that are not the working tree file of the same path.

    The base supplies nothing: nothing of the target is hidden from the tree,
    which is the truth for every target whose files are its own. A subclass
    supplies what it knows, and a job asks it for every fact it would
    otherwise read from the disk.

    Supplying nothing and supplying something is the whole difference between
    the two sides: a side that names records is a description written by
    something other than a pack flow, so the pack claims about them are
    advisory, see UpdateJob._validate_update_records().

    Attributes:
        version (str): Version the local files record, '' when they name none
        fileinfo (dict[str, IdxInfo]): Records of the files of the version
        filedata (dict[str, bytes]): Content of the files, keyed by the path
            of the target
    """

    # version of the target the local files record, '' when they name none
    version = ''
    # {path: IdxInfo} of the files of the version
    fileinfo: "dict[str, IdxInfo]" = {}
    # {path: content} of the files, in the shape the pack of the version
    # records them
    filedata: "dict[str, bytes]" = {}


class LocalSideRepo(LocalSide):
    """
    A target whose files are its own working tree and its ledger.

    Nothing is hidden from the tree, so every member is the empty value of the
    base class: the flow reads the files of the target as they are.
    """

    SCENE = 'repo'


class LocalSidePip(LocalSide):
    """
    The installation of a python distribution behind a target.

    The caller passes the target of a distribution it took over from pip: the
    folder the installation lives in and the name of the distribution. Nothing
    is read here: every fact is a cached property, computed on the first use
    of a flow that needs it, so a check of a target that only compares
    versions touches no file of the installation.

    Attributes:
        root (PathStr): Folder of the installation, the root of the target
        name (str): Name of the distribution, any case and separator style
    """

    SCENE = 'pypi'

    def __init__(self, root, name):
        """
        Args:
            root (str | PathStr): Folder the distribution is installed in, the
                root of the target
            name (str): Name of the distribution, any case and separator
                style: the ledger key of its target is the PEP 503 normalized
                form
        """
        self.root = PathStr.new(root)
        self.name = name

    def __repr__(self):
        """
        Returns:
            str: Representation of the installation
        """
        return f'{type(self).__name__}({self.name!r})'

    @cached_property
    def dist(self):
        """
        Returns:
            Distribution | None: The installation of the distribution, None
                when the folder holds none
        """
        return PipList(self.root).find(normalize_name(self.name))

    @cached_property
    def record_rel(self):
        """
        Returns:
            str: Path of the RECORD relative to the root, '' when the
                installation has no .dist-info entry (a legacy .egg-info
                installation records no file)
        """
        dist = self.dist
        if dist is None:
            return ''
        info = str(dist.info)
        if not info.endswith(DIST_INFO_SUFFIX):
            return ''
        rel = subpath_to(info, str(self.root))
        if not rel:
            return ''
        return f'{rel}/{RECORD_FILE}'

    @cached_property
    def record(self):
        """
        Returns:
            RecordManager | None: The RECORD of the installation, None when
                the installation has none that can be read
        """
        if not self.record_rel:
            return None
        try:
            content = atomic_read_bytes(self.root.joinpath(self.record_rel))
        except OSError:
            # missing, unreadable, a directory
            return None
        record = RecordManager()
        try:
            record.load_bytes(content)
        except UnicodeDecodeError:
            # the RECORD is a UTF-8 csv, PEP 376
            return None
        return record

    @cached_property
    def version(self):
        """
        Returns:
            str: Version of the installation, '' when it holds no RECORD: a
                target whose files are unknown may not be updated
                incrementally
        """
        if self.record is None:
            return ''
        return self.dist.version

    @cached_property
    def fileinfo(self):
        """
        The records of the files of the installation.

        Only the size of a record is read from the RECORD: the sha1 of the
        pack format is of the content, which the RECORD does not record (it
        holds sha256). The content is read by the job that uses a record, see
        JobBase._local_side_data(), and a record whose content does not match
        the pack is downloaded instead of being patched.

        Returns:
            dict[str, IdxInfo]: Records keyed by the path relative to the
                root, {} when the installation holds no RECORD
        """
        record = self.record
        if record is None:
            return {}
        return {
            path: IdxInfo(path=path, size=self._recorded_size(entry))
            for path, entry in record.entries.items()
            if self._packed(path)
        }

    @cached_property
    def filedata(self):
        """
        Returns:
            dict[str, bytes]: The RECORD of the pack of the version, the one
                file an installation writes differently from the pack, {}
                when the installation holds no RECORD
        """
        record = self.record
        if record is None:
            return {}
        return {self.record_rel: self.pack_record(record)}

    @staticmethod
    def pack_record(record):
        """
        The RECORD of an installation in the shape the pack of the version
        records it.

        The rows of the bytecode an installer compiled and of the files it
        wrote outside of the site-packages folder are not part of the pack of
        a distribution, so a record that patches the RECORD of a pack can not
        use the file of the installation as it is.

        Args:
            record (RecordManager): The RECORD of an installation

        Returns:
            bytes: Content of the RECORD of the pack of the version
        """
        pack = RecordManager()
        pack.entries = {
            path: entry
            for path, entry in record.entries.items()
            if LocalSidePip._packed(path) and not path.endswith(PYC_SUFFIX)
        }
        return pack.dump_bytes()

    @staticmethod
    def _packed(path):
        """
        Check whether a RECORD path is a file of the pack of the version.

        The RECORD lists every file of an installation with the path relative
        to the site-packages folder, PEP 376; a file outside of it is recorded
        with "..". The pack of a distribution holds the files of the folder
        only, so such a path describes no file of the old version.

        Args:
            path (str): Path of a RECORD entry

        Returns:
            bool: True when the path is a file of the pack
        """
        if not path or path.startswith('/'):
            return False
        parts = path.split('/')
        if parts[0] in (OUTSIDE_COMPONENT, '') or ':' in parts[0]:
            # a path outside the folder, or an absolute path of a tool that
            # records one, e.g. "C:/..."
            return False
        return True

    @staticmethod
    def _recorded_size(entry):
        """
        Size of a RECORD entry as an int.

        Args:
            entry (RecordEntry): Row of a RECORD

        Returns:
            int: Size of the file, 0 when the row records none, e.g. the row
                of the RECORD itself
        """
        if entry.size.isdigit():
            return int(entry.size)
        return 0


# The local sides a job can be given, by the scene name of its channel: the
# files of a mod tree are the tree itself, the files of a python distribution
# are the ones its installer wrote.
SCENES = {
    LocalSideRepo.SCENE: LocalSideRepo,
    LocalSidePip.SCENE: LocalSidePip,
}


def create_local_side(scene, root, name=''):
    """
    Create the local side of a target.

    The project tree target (an empty name) manages no distribution: it is
    always the repository side, whatever the caller declares, so no path of a
    site-packages folder is ever touched by its flows.

    Args:
        scene (str): Scene name of the target channel, see SCENES
        root (str | PathStr): Root of the target
        name (str): Ledger key of the target. Defaults to ''

    Returns:
        LocalSide: The local side of the target

    Raises:
        ValueError: If the scene is not a known one
    """
    if not name:
        return LocalSideRepo()
    try:
        side = SCENES[scene]
    except KeyError:
        raise ValueError(
            f'Unknown scene: {scene!r}, expected one of {sorted(SCENES)}'
        ) from None
    if side is LocalSideRepo:
        return LocalSideRepo()
    return side(root, name)
