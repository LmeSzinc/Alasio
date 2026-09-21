import hashlib
from collections import deque

from alasio.deploy.pack.pack_model import RefInfo
from alasio.ext.algorithm.pathcomb import iter_path_comb
from alasio.ext.algorithm.pathlen_coding import encode_prefix_comb, encode_suffix_comb
from alasio.ext.algorithm.vlenint import encode_vlenint
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

        # filepath
        list_path: "deque[bytes]" = deque()
        list_prefix_reuse = deque()
        list_suffix_lookback = deque()
        list_suffix_reuse = deque()
        for prefix_reuse, path, suffix_reuse, suffix_lookback in iter_path_comb(self.files):
            list_prefix_reuse.append(prefix_reuse)
            list_suffix_lookback.append(suffix_lookback)
            list_suffix_reuse.append(suffix_reuse)
            # remaining path, empty when fully reused by prefix + suffix
            list_path.append(path.encode())

        # remaining path byte lengths, 0 for fully reused paths
        list_path_length = [len(path) for path in list_path]

        list_prefix_comb = encode_prefix_comb(list_prefix_reuse, list_path_length)
        yield encode_vlenint(list_prefix_comb)
        list_suffix_comb = encode_suffix_comb(list_suffix_reuse, list_suffix_lookback)
        yield encode_vlenint(list_suffix_comb)
        yield b''.join(list_path)

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
