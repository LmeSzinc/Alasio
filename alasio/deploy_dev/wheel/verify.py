"""
Verify the distribution artifacts of the project, the release gate of the
wheel job.

The verifier reads an artifact from disk and checks what the build promises:

- the archive is not corrupt;
- the metadata of the wheel names the same distribution as the file name;
- the RECORD lists every member and every hash matches the content;
- the frontend build is complete and its manifest agrees with the files;
- the webapp app.asar keeps the layout ``package.json.main`` expects;
- the sdist carries the files needed to build the project.

A failure raises ``WheelVerifyError`` with every problem found, so a caller
reports all of them in one run instead of one per build.
"""
import io
import json
import struct
import tarfile
import zipfile
from email.parser import BytesParser
from hashlib import sha1

import msgspec
from packaging.utils import canonicalize_name

from alasio.backport import removesuffix
from alasio.codegen.asar import KIND_FILE
from alasio.codegen.asar.errors import AsarError
from alasio.codegen.asar.format import ALIGNMENT, MAX_HEADER_SIZE, SIZE_PICKLE_PAYLOAD
from alasio.codegen.asar.model import path_keys, read_entries
from alasio.deploy.pack.decode_base import PackDecodeError
from alasio.deploy.pack.decode_manifest import PackDecodeManifest
from alasio.deploy.simple_pip.whl_record import RecordManager, sha256_checksum
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes

# Layout of the deploy data inside the wheel, see RepoWithStatics.static_files
DEPLOY_DATA = 'deploy_data'
FRONTEND_FOLDER = f'{DEPLOY_DATA}/frontend'
FRONTEND_MANIFEST = f'{DEPLOY_DATA}/frontend-manifest.pack'
WEBAPP_ASAR = f'{DEPLOY_DATA}/webapp/app.asar'
INDEX_HTML = 'index.html'

# The build outputs the wheel job downloads before the build. A wheel built
# without them is a valid wheel that carries no frontend / no webapp, which is
# never the artifact of a release.
BUILD_INPUTS = (
    'frontend/build/index.html',
    'frontend/build/_app/version.json',
    'webapp/dist/main/index.js',
    'webapp/dist/preload/index.js',
    'webapp/dist/renderer/index.html',
    'webapp/package.json',
)

WHEEL_SUFFIX = '.whl'
SDIST_SUFFIX = '.tar.gz'


class WheelVerifyError(Exception):
    """
    Raised when an artifact fails verification.

    Attributes:
        problems (list[str]): Every problem found, in the order they were found
    """

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems))


class ArtifactReport:
    """
    Summary of an artifact that passed verification.

    Attributes:
        file (str): Path of the artifact
        kind (str): "wheel" or "sdist"
        name (str): Distribution name
        version (str): Distribution version
        files (int): Number of files the artifact carries
        extra (str): Additional detail for the log, "" when there is none
    """

    def __init__(self, file, kind, name, version, files, extra=''):
        self.file = str(file)
        self.kind = kind
        self.name = name
        self.version = version
        self.files = files
        self.extra = extra

    def __repr__(self):
        extra = f', {self.extra}' if self.extra else ''
        return f'{self.kind} {self.name}=={self.version} ({self.files} files{extra}): {self.file}'


class WheelVerifier:
    """
    Verify one built wheel.

    The attributes are the state of the verification of one file, so the
    checks read them instead of passing the archive, the member set and the
    problem list through every call.

    Attributes:
        file (str): Path of the wheel
        folder (str): Top level folder of the library in the wheel
        problems (list[str]): Every problem found
        zf (zipfile.ZipFile): The archive, open while verify() runs
        members (set[str]): File members of the wheel, the directories dropped
        dist_info (str): The single dist-info folder of the wheel
        name (str): Distribution name from METADATA
        version (str): Distribution version from METADATA
        frontend_files (int): Number of files the frontend manifest lists
        asar_entries (int): Number of entries of app.asar
    """

    def __init__(self, file, folder='alasio'):
        """
        Args:
            file (str): Path of the wheel
            folder (str): Top level folder of the library in the wheel
        """
        self.file = file
        self.folder = folder
        self.problems = []
        self.zf = None
        self.members = set()
        self.dist_info = ''
        self.name = ''
        self.version = ''
        self.frontend_files = 0
        self.asar_entries = 0

    def verify(self):
        """
        Run every check of the wheel.

        Returns:
            ArtifactReport: Summary of the wheel

        Raises:
            WheelVerifyError: If the wheel does not pass
        """
        try:
            data = atomic_read_bytes(self.file)
        except OSError as e:
            raise WheelVerifyError([f'Failed to read the wheel {self.file}: {e}'])
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise WheelVerifyError([f'{self.file} is not a zip archive: {e}'])

        with zf:
            bad = zf.testzip()
            if bad is not None:
                raise WheelVerifyError([f'{self.file} has a corrupt member: {bad}'])
            self.zf = zf
            self.members = {name for name in zf.namelist() if not name.endswith('/')}
            self.dist_info = self._dist_info()
            self._check_metadata()
            self._check_wheel_file()
            self._check_record()
            self._check_required_members()
            self._check_frontend()
            self._check_asar()

        if self.problems:
            raise WheelVerifyError(self.problems)
        return ArtifactReport(
            file=self.file,
            kind='wheel',
            name=self.name,
            version=self.version,
            files=len(self.members),
            extra=f'frontend {self.frontend_files} files, asar {self.asar_entries} entries',
        )

    def _dist_info(self):
        """
        Find the single dist-info folder of the wheel.

        Returns:
            str: Folder name, e.g. "alasio-2.0.0.dist-info"

        Raises:
            WheelVerifyError: If the wheel does not have exactly one dist-info
                folder with a METADATA file at its root
        """
        folders = sorted({
            name[:-len('/METADATA')]
            for name in self.members
            if name.endswith('.dist-info/METADATA') and name.count('/') == 1
        })
        if len(folders) != 1:
            raise WheelVerifyError([
                f'{self.file} must have exactly one .dist-info/METADATA member, found {len(folders)}'
            ])
        return folders[0]

    def _check_metadata(self):
        """
        Check METADATA against the dist-info folder and the wheel file name.
        """
        metadata = BytesParser().parsebytes(self.zf.read(f'{self.dist_info}/METADATA'))
        self.name = str(metadata.get('Name', ''))
        self.version = str(metadata.get('Version', ''))
        if not self.name:
            self.problems.append(f'METADATA of {self.file} has no Name')
        if not self.version:
            self.problems.append(f'METADATA of {self.file} has no Version')
        if not (self.name and self.version):
            return

        stem = self.dist_info[:-len('.dist-info')]
        expected_stem = f'{canonicalize_name(self.name).replace("-", "_")}-{self.version}'
        expected_file = f'{expected_stem}-py3-none-any.whl'
        if stem != expected_stem:
            self.problems.append(
                f'METADATA names {self.name}=={self.version}, the wheel folder is "{self.dist_info}"'
            )
        if PathStr.new(self.file).name != expected_file:
            self.problems.append(
                f'METADATA names {self.name}=={self.version}, the wheel file is "{PathStr.new(self.file).name}"'
            )

    def _check_wheel_file(self):
        """
        Check the WHEEL file of the distribution.
        """
        path = f'{self.dist_info}/WHEEL'
        try:
            content = self.zf.read(path)
        except KeyError:
            self.problems.append(f'Missing WHEEL: {path}')
            return
        lines = {line.strip() for line in content.decode('utf-8', errors='replace').splitlines()}
        if 'Tag: py3-none-any' not in lines:
            self.problems.append(f'{path} does not declare the tag py3-none-any')
        if 'Root-Is-Purelib: true' not in lines:
            self.problems.append(f'{path} does not declare Root-Is-Purelib: true')

    def _check_record(self):
        """
        Check the RECORD against the members of the wheel.

        Every member has to be listed with its real size and sha256, and every
        listed file has to be in the wheel, which is what an installer trusts.
        """
        path = f'{self.dist_info}/RECORD'
        try:
            content = self.zf.read(path)
        except KeyError:
            self.problems.append(f'Missing RECORD: {path}')
            return
        record = RecordManager()
        try:
            record.load_bytes(content)
        except UnicodeDecodeError as e:
            self.problems.append(f'RECORD is not UTF-8: {e}')
            return

        for name in sorted(self.members):
            if name == path:
                continue
            entry = record.entries.get(name)
            if entry is None:
                self.problems.append(f'File is not listed in RECORD: {name}')
                continue
            data = self.zf.read(name)
            if entry.size:
                try:
                    size = int(entry.size)
                except ValueError:
                    self.problems.append(f'RECORD has an invalid size for {name}: "{entry.size}"')
                else:
                    if size != len(data):
                        self.problems.append(
                            f'RECORD size mismatch for {name}: {size} recorded, {len(data)} actual'
                        )
            if entry.sha256:
                expected = f'sha256={sha256_checksum(data)}'
                if entry.sha256 != expected:
                    self.problems.append(f'RECORD hash mismatch for {name}')
        for name in sorted(record.entries):
            if name not in self.members:
                self.problems.append(f'RECORD lists a file that is not in the wheel: {name}')

    def _check_required_members(self):
        """
        Check the frontend and the webapp members of the release wheel.
        """
        for path in (
            f'{self.folder}/{FRONTEND_FOLDER}/{INDEX_HTML}',
            f'{self.folder}/{FRONTEND_MANIFEST}',
            f'{self.folder}/{WEBAPP_ASAR}',
        ):
            if path not in self.members:
                self.problems.append(f'Missing required member: {path}')

    def _check_frontend(self):
        """
        Check the frontend files against their manifest.

        The backend serves the frontend from the manifest only, a file that is
        missing or does not match its record is a broken deployment.
        """
        manifest = f'{self.folder}/{FRONTEND_MANIFEST}'
        if manifest not in self.members:
            return

        prefix = f'{self.folder}/{FRONTEND_FOLDER}/'
        try:
            decoder = PackDecodeManifest(self.zf.read(manifest))
            decoder.validate()
            records = decoder.files
        except PackDecodeError as e:
            self.problems.append(f'Frontend manifest is invalid: {e}')
            return
        self.frontend_files = len(records)

        for path, record in records.items():
            if not path.startswith(prefix):
                self.problems.append(f'Frontend manifest lists a file outside of "{prefix}": {path}')
                continue
            if path not in self.members:
                self.problems.append(f'Frontend file listed in the manifest is missing: {path}')
                continue
            content = self.zf.read(path)
            digest = sha1(content).digest()
            if len(content) != record.size or digest != record.sha1:
                self.problems.append(
                    f'Frontend file does not match the manifest: "{path}" is '
                    f'{len(content)} bytes with sha1 {digest.hex()}, '
                    f'expected {record.size} bytes with sha1 {record.sha1.hex()}'
                )

        index = f'{prefix}{INDEX_HTML}'
        if index not in records:
            self.problems.append(f'Frontend build has no {INDEX_HTML}: {index}')
        for name in self.members:
            if name.startswith(prefix) and name not in records:
                self.problems.append(f'Frontend file is not listed in the manifest: {name}')

    def _check_asar(self):
        """
        Check the webapp app.asar against the layout of the packer.
        """
        path = f'{self.folder}/{WEBAPP_ASAR}'
        if path not in self.members:
            return
        try:
            self.asar_entries = AsarVerifier(self.zf.read(path)).verify()
        except WheelVerifyError as e:
            self.problems.extend(e.problems)


class AsarVerifier:
    """
    Verify the app.asar a wheel carries.

    The archive has to keep the layout electron-builder produces, the files
    under ``dist/`` and ``package.json`` at the root, because
    ``package.json.main`` points into ``dist/``. An archive whose main entry
    does not resolve is an app that can not start.

    Attributes:
        data (bytes): Content of the archive
        problems (list[str]): Every problem found
        entries (dict): Flat entry table, see ``read_entries()``
        data_offset (int): Offset of the first content byte
    """

    def __init__(self, data):
        """
        Args:
            data (bytes): Content of the archive
        """
        self.data = data
        self.problems = []
        self.entries = {}
        self.data_offset = 0

    def verify(self):
        """
        Run every check of the archive.

        Returns:
            int: Number of entries

        Raises:
            WheelVerifyError: If the archive is malformed, or its layout is not
                the one a wheel can carry
        """
        self._read_entries()
        self._check_entries()
        self._check_package()
        if self.problems:
            raise WheelVerifyError(self.problems)
        return len(self.entries)

    def _read_entries(self):
        """
        Decode the archive and build its entry table.

        Raises:
            WheelVerifyError: If the frame or the table is invalid
        """
        try:
            header, self.data_offset = _decode_asar_header(self.data)
            self.entries = read_entries(header, len(self.data), self.data_offset, '')
        except (AsarError, ValueError, struct.error, msgspec.DecodeError, RecursionError) as e:
            raise WheelVerifyError([f'app.asar is not a valid archive: {e}'])

    def _check_entries(self):
        """
        Check the layout of the entry table.
        """
        for keys, info in self.entries.items():
            path = '/'.join(keys)
            if info.unpacked:
                self.problems.append(f'app.asar entry is unpacked, the file is not in the archive: {path}')
            if path != 'package.json' and keys[0] != 'dist':
                self.problems.append(f'app.asar entry is outside of "dist/": {path}')

    def _check_package(self):
        """
        Check that package.json.main resolves to a file of the archive.
        """
        package = self.entries.get(('package.json',))
        if package is None:
            self.problems.append('app.asar has no package.json')
            return
        if package.kind != KIND_FILE:
            self.problems.append('app.asar package.json is not a file')
            return
        if package.unpacked:
            # the unpacked file lives next to the archive, the wheel has no copy
            return

        start = self.data_offset + package.offset
        content = self.data[start:start + package.size]
        main = None
        try:
            main = json.loads(content).get('main')
        except (AttributeError, json.JSONDecodeError) as e:
            self.problems.append(f'app.asar package.json is invalid: {e}')
        if main is None:
            self.problems.append('app.asar package.json has no main')
        elif not isinstance(main, str) or not main:
            self.problems.append(f'app.asar package.json main is not a path: {main!r}')
        else:
            target = self.entries.get(path_keys(main))
            if target is None:
                self.problems.append(f'app.asar package.json main is missing: {main}')
            elif target.kind != KIND_FILE:
                self.problems.append(f'app.asar package.json main is not a file: {main}')


def _decode_asar_header(data):
    """
    Decode the header of an asar archive held in memory.

    ``format.read_header()`` reads a real file handle, it needs ``os.fstat``
    for the size of the archive, while the archive of a wheel is a member of a
    zip file. The frame is decoded here, the entry table is built by
    ``model.read_entries()``, the same code the file reader uses.

    Args:
        data (bytes): Content of the archive

    Returns:
        tuple[dict, int]: Header JSON, and the offset of the data area

    Raises:
        ValueError: If the frame is malformed
        msgspec.DecodeError: If the header JSON is invalid
    """
    if len(data) < 16:
        raise ValueError(f'archive is truncated, expected 16 bytes but got {len(data)}')
    payload, header_size = struct.unpack_from('<II', data, 0)
    if payload != SIZE_PICKLE_PAYLOAD:
        raise ValueError(
            f'broken size pickle, expected a payload of {SIZE_PICKLE_PAYLOAD} bytes, got {payload}'
        )
    if header_size < 8:
        raise ValueError(f'header size {header_size} is smaller than the 8 bytes a header pickle needs')
    if header_size > MAX_HEADER_SIZE:
        raise ValueError(f'header size {header_size} exceeds the {MAX_HEADER_SIZE} bytes limit')
    if header_size + 8 > len(data):
        raise ValueError(f'header size {header_size} exceeds the archive size of {len(data)} bytes')
    pickle = data[8:8 + header_size]
    payload, json_size = struct.unpack_from('<II', pickle, 0)
    if payload + 4 > header_size:
        raise ValueError(
            f'header pickle is truncated, payload claims {payload} bytes, got {header_size - 4}'
        )
    if ALIGNMENT + ((json_size + 3) & ~3) > payload:
        raise ValueError(
            f'header JSON does not fit in the header pickle, '
            f'JSON claims {json_size} bytes, payload is {payload}'
        )
    return msgspec.json.decode(pickle[8:8 + json_size]), 8 + header_size


class SdistVerifier:
    """
    Verify one built sdist.

    Attributes:
        file (str): Path of the sdist
        folder (str): Top level folder of the library in the sdist
        problems (list[str]): Every problem found
        base (str): Root folder of the archive, from the file name
        members (set[str]): Member names of the archive
        name (str): Distribution name from PKG-INFO
        version (str): Distribution version from PKG-INFO
        pkg_info (bytes): Content of PKG-INFO, None when it is missing
    """

    def __init__(self, file, folder='alasio'):
        """
        Args:
            file (str): Path of the sdist
            folder (str): Top level folder of the library in the sdist
        """
        self.file = file
        self.folder = folder
        self.problems = []
        self.base = removesuffix(PathStr.new(file).name, SDIST_SUFFIX)
        self.members = set()
        self.name = ''
        self.version = ''
        self.pkg_info = None

    def verify(self):
        """
        Run every check of the sdist.

        Returns:
            ArtifactReport: Summary of the sdist

        Raises:
            WheelVerifyError: If the sdist does not pass
        """
        self._read()
        self._check_members()
        self._check_metadata()
        if self.problems:
            raise WheelVerifyError(self.problems)
        return ArtifactReport(
            file=self.file,
            kind='sdist',
            name=self.name,
            version=self.version,
            files=len(self.members),
        )

    def _read(self):
        """
        Read the archive and the PKG-INFO of the sdist.

        Raises:
            WheelVerifyError: If the file is not a readable tar.gz
        """
        try:
            data = atomic_read_bytes(self.file)
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
                infos = {info.name: info for info in tar.getmembers()}
                self.members = set(infos)
                info = infos.get(f'{self.base}/PKG-INFO')
                self.pkg_info = tar.extractfile(info).read() if info is not None and info.isfile() else None
        except (OSError, tarfile.TarError) as e:
            raise WheelVerifyError([f'{self.file} is not a readable sdist: {e}'])

    def _check_members(self):
        """
        Check the member list and the files the sdist has to carry.
        """
        for member in self.members:
            if member.startswith('/') or '..' in member.split('/'):
                self.problems.append(f'sdist member is outside of the archive root: {member}')

        for member in (f'{self.base}/PKG-INFO', f'{self.base}/pyproject.toml'):
            if member not in self.members:
                self.problems.append(f'Missing sdist member: {member}')

        package_prefix = f'{self.base}/{self.folder}/'
        if not any(member.startswith(package_prefix) for member in self.members):
            self.problems.append(f'sdist carries no file of the library: {package_prefix}')

    def _check_metadata(self):
        """
        Check PKG-INFO against the sdist file name.
        """
        if self.pkg_info is None:
            return
        metadata = BytesParser().parsebytes(self.pkg_info)
        self.name = str(metadata.get('Name', ''))
        self.version = str(metadata.get('Version', ''))
        if not self.name:
            self.problems.append(f'PKG-INFO of {self.file} has no Name')
        if not self.version:
            self.problems.append(f'PKG-INFO of {self.file} has no Version')
        if self.name and self.version:
            expected = f'{canonicalize_name(self.name)}-{self.version}{SDIST_SUFFIX}'
            if PathStr.new(self.file).name != expected:
                self.problems.append(
                    f'PKG-INFO names {self.name}=={self.version}, '
                    f'the sdist file is "{PathStr.new(self.file).name}"'
                )


def verify_artifact(file, folder='alasio'):
    """
    Verify one distribution artifact, the type is read from the file name.

    Args:
        file (str): Path of a wheel or an sdist
        folder (str): Top level folder of the library in the wheel

    Returns:
        ArtifactReport: Summary of the artifact

    Raises:
        WheelVerifyError: If the file is not an artifact, or does not pass
    """
    name = PathStr.new(file).name
    if name.endswith(WHEEL_SUFFIX):
        return WheelVerifier(file, folder=folder).verify()
    if name.endswith(SDIST_SUFFIX):
        return SdistVerifier(file, folder=folder).verify()
    raise WheelVerifyError([f'Not a wheel or an sdist: {file}'])


def verify_wheel(file, folder='alasio'):
    """
    Verify a built wheel.

    Args:
        file (str): Path of the wheel
        folder (str): Top level folder of the library in the wheel

    Returns:
        ArtifactReport: Summary of the wheel

    Raises:
        WheelVerifyError: If the wheel does not pass
    """
    return WheelVerifier(file, folder=folder).verify()


def verify_sdist(file, folder='alasio'):
    """
    Verify a built sdist.

    Args:
        file (str): Path of the sdist
        folder (str): Top level folder of the library in the sdist

    Returns:
        ArtifactReport: Summary of the sdist

    Raises:
        WheelVerifyError: If the sdist does not pass
    """
    return SdistVerifier(file, folder=folder).verify()


def verify_asar(data):
    """
    Verify the app.asar that the wheel carries.

    Args:
        data (bytes): Content of the archive

    Returns:
        int: Number of entries

    Raises:
        WheelVerifyError: If the archive is malformed, or its layout is not
            the one a wheel can carry
    """
    return AsarVerifier(data).verify()


def check_build_inputs(root):
    """
    Check the build outputs the wheel is assembled from.

    Args:
        root (str): Project root

    Returns:
        list[str]: Problems, empty when every input is there
    """
    root = PathStr.new(root)
    return [
        f'Missing build input: {path}'
        for path in BUILD_INPUTS
        if not (root / path).isfile()
    ]


def collect_artifacts(paths):
    """
    Collect the artifacts to verify.

    Args:
        paths (list[str]): Artifact files, or directories that hold them

    Returns:
        tuple[list[str], list[str]]: Artifact paths sorted by name, and the
            problems with the paths that are neither an artifact nor a
            directory
    """
    files = set()
    problems = []
    for path in paths:
        p = PathStr.new(path)
        if p.isdir():
            for file in p.iter_files():
                if file.endswith((WHEEL_SUFFIX, SDIST_SUFFIX)):
                    files.add(str(file))
        elif p.isfile() and p.endswith((WHEEL_SUFFIX, SDIST_SUFFIX)):
            files.add(str(p))
        else:
            problems.append(f'Not a wheel, an sdist, or a directory that holds them: {path}')
    return sorted(files), problems
