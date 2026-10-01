import hashlib

from alasio.deploy.pack.pack_model import RefInfo
from alasio.ext.algorithm.bit2coding.vlenint_encode_c import encode_vlenint
from alasio.ext.algorithm.pathcomb.pathcomb_encode_c import encode_path_comb
from alasio.ext.path.validate import validate_filepath


def encode_manifest_version(manifest_version):
    """
    Encode a manifest format version into the single byte of the manifest header.

    The version is an int in Python (PackEncodeManifest.manifest_version) and
    one byte in a manifest file, the byte behind b'MANI', so 0~255 is the whole
    range the format carries.

    Args:
        manifest_version (int): Manifest format version, 0~255

    Returns:
        bytes: The single byte of the version

    Raises:
        ValueError: If manifest_version is not an int in 0~255
    """
    if not isinstance(manifest_version, int) or not 0 <= manifest_version <= 0xFF:
        raise ValueError(
            f'Manifest version must be an int in 0~255, got {manifest_version!r}')
    return bytes((manifest_version,))


class PackEncodeManifest:
    """
    # header
    - b'MANI'
    - MANIFEST version, one byte, an int in 0~255 on the Python side

    # data section
    - filepath
    - size
    - sha1, the 20 bytes digest of the file content
    - checksum of above

    Attributes:
        files (dict[str, RefInfo]): {filepath: RefInfo} records to encode.
        manifest_version (int): MANIFEST format version, 0~255, written to
            the header of the manifest as its single byte.
    """

    def __init__(self):
        self.files: "dict[str, RefInfo]" = {}
        self.manifest_version = 0

    @property
    def manifest_version(self):
        """
        Manifest format version of this manifest, an int in 0~255

        Returns:
            int: Manifest format version
        """
        return self._manifest_version

    @manifest_version.setter
    def manifest_version(self, manifest_version):
        # the encode of the header byte, run here as the check: an out of range
        # version fails at the assignment, not at the assembly of the manifest
        encode_manifest_version(manifest_version)
        self._manifest_version = manifest_version

    def add_file(self, path, content):
        """
        Args:
            path (str):
            content (bytes | memoryview):
        """
        validate_filepath(path)
        size = len(content)
        sha1 = hashlib.sha1(content).digest()
        self.files[path] = RefInfo(path=path, size=size, sha1=sha1)

    def iter_data(self):
        yield b'MANI'
        # version
        yield encode_manifest_version(self.manifest_version)

        # filepath, the resulting sections are written one after another
        list_prefix_comb, list_suffix_comb, path_data = encode_path_comb(self.files)
        yield encode_vlenint(list_prefix_comb)
        yield encode_vlenint(list_suffix_comb)
        yield path_data

        # size
        list_size = [file.size for file in self.files.values()]
        yield encode_vlenint(list_size)

        # sha1, the raw digest of the file content
        for file in self.files.values():
            # this shouldn't happen
            if not file.sha1:
                raise ValueError(f'Empty sha1 from {file}')
            yield file.sha1

    def iter_manifest_data(self):
        checksum = hashlib.sha1()
        for row in self.iter_data():
            yield row
            checksum.update(row)

        yield checksum.digest()
