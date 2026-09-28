import hashlib

from alasio.deploy.pack.pack_model import RefInfo
from alasio.ext.algorithm.bit2coding.vlenint_encode_c import encode_vlenint
from alasio.ext.algorithm.pathcomb.pathcomb_encode_c import encode_path_comb
from alasio.ext.path.validate import validate_filepath


class PackEncodeManifest:
    """
    # header
    - b'MANI'
    - MANIFEST version

    # data section
    - filepath
    - size
    - sha1, the 20 bytes digest of the file content
    - checksum of above

    Attributes:
        files (dict[str, RefInfo]): {filepath: RefInfo} records to encode.
        manifest_version (bytes): MANIFEST format version byte, written
            to the header of the manifest.
    """

    def __init__(self):
        self.files: "dict[str, RefInfo]" = {}
        self.manifest_version = b'\x00'

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
        yield self.manifest_version

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
