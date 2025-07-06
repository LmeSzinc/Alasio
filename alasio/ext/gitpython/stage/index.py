import struct

import msgspec

from alasio.ext.path.atomic import atomic_read_bytes, atomic_write


class GitIndexBroken(Exception):
    pass


class GitIndexEntry(msgspec.Struct):
    ctime_s: int
    ctime_ns: int
    mtime_s: int
    mtime_ns: int
    dev: int
    ino: int
    mode: int
    uid: int
    gid: int
    size: int
    sha1: bytes
    flags: int
    path: str


class GitIndex:
    def __init__(self, file):
        """
        Args:
            file (str): Absolute path to .git/index
        """
        self.file = file
        # all indexed files in workspace
        self.list_entry: "list[GitIndexEntry]" = []
        # git index version, 2, 3, 4, or 0 if not loaded
        self.version = 0

    def clear_object(self):
        self.list_entry = []
        self.version = 0

    def index_read(self, file=None, data=None):
        """
        Read .git/index file and parse it

        Args:
            file (str): Specify file to read, None to use current file
            data (bytes): Specify content to read, None to read from current file

        Returns:
            int: Amount of hashes in .git/index file

        Raises:
            FileNotFoundError:
            GitIndexBroken:
        """
        if file is None:
            file = self.file
        if data is None:
            data = atomic_read_bytes(file)

        try:
            signature, version, entry_count = struct.unpack('>4sII', data[:12])
        except struct.error as e:
            raise GitIndexBroken(f'Unexpected header: {data[:12]}, {e}')
        # index file must have signature DIRC
        if signature != b'DIRC':
            raise GitIndexBroken(f'Invalid signature: {signature!r}')
        self.version = version

        data = memoryview(data)
        if version in [2, 3]:
            self.list_entry = list(self._parse_v2(data, entry_count))
        elif version in [4]:
            self.list_entry = list(self._parse_v4(data, entry_count))
        else:
            # we only support v2, v3, v4
            raise GitIndexBroken(f'Unsupported version: {version}')

    @staticmethod
    def _parse_v2(data, entry_count):
        """
        Parse git index file of v2 and v3

        Args:
            data (memoryview):
            entry_count (int):

        Yields:
            GitIndexEntry:
        """
        # https://git-scm.com/docs/index-format
        unpacker = struct.Struct('>IIIIIIIIII20sH').unpack
        # 12bytes header
        cursor = 12
        total_len = len(data)

        for i in range(entry_count):
            # unpack fixed part
            path_start = cursor + 62
            try:
                fixed_part = unpacker(data[cursor:path_start])
            except struct.error:
                raise GitIndexBroken(f'File ended prematurely on entry {i} fixed part: '
                                     f'{data[cursor:path_start].hex()}')
            # ctime_s, ctime_ns, mtime_s, mtime_ns, dev, ino, mode, uid, gid, size, sha1, flags = fixed_part
            flags = fixed_part[11]

            # calculate path
            path_len = flags % 4096
            if path_len == 4095:
                raise GitIndexBroken(f'Filepath length > 4095 bytes on entry {i}')
            if path_len == 0:
                raise GitIndexBroken(f'Path length is 0 on entry {i}: {fixed_part}')
            path_end = path_start + path_len
            if path_end > total_len:
                raise GitIndexBroken(f'File ended prematurely reading path for entry {i}')

            path = data[path_start:path_end].tobytes()
            try:
                path = path.decode('utf-8')
            except UnicodeDecodeError as e:
                raise GitIndexBroken(f'Filepath invalid on entry {i}, path={path}, {e}')

            # Build info
            yield GitIndexEntry(*fixed_part, path)

            # calculate next cursor
            # path is align to 8 bytes
            cursor += (70 + path_len) // 8 * 8

    @staticmethod
    def _parse_v4(data, entry_count):
        """
        Parse git index file of v4

        Args:
            data (memoryview):
            entry_count (int):

        Yields:
            GitIndexEntry:
        """
        unpacker = struct.Struct('>IIIIIIIIII20sH').unpack
        # 12bytes header
        cursor = 12
        # v4 reuses previous path
        prev_path = b''

        for i in range(entry_count):
            # unpack fixed part
            path_start = cursor + 62
            try:
                fixed_part = unpacker(data[cursor:path_start])
            except struct.error:
                raise GitIndexBroken(f'File ended prematurely on entry {i} fixed part: '
                                     f'{data[cursor:path_start].hex()}')
            # ctime_s, ctime_ns, mtime_s, mtime_ns, dev, ino, mode, uid, gid, size, sha1, flags = fixed_part
            flags = fixed_part[11]

            # calculate path
            # path_len is the final length
            path_len = flags % 4096
            if path_len == 4095:
                raise GitIndexBroken(f'Filepath length > 4095 bytes on entry {i}')
            if path_len == 0:
                raise GitIndexBroken(f'Path length is 0 on entry {i}: {fixed_part}')

            # remove N bytes from previous path
            # <varint_N><suffix_S>\x00
            # same as OFS_DELTA
            offset = 0
            index = 1
            for byte in data[path_start:]:
                # add in the next 7 bits of data
                if byte >= 128:
                    offset += byte - 127
                    offset *= 128
                    index += 1
                else:
                    # end reverse_offset, start source_size
                    offset += byte
                    break

            path_start += index
            # keep bytes from prev filepath
            # b".gitattributes" is in length of 14, offset=10, keep_prev=4
            keep_prev = len(prev_path) - offset
            path_end = path_start + path_len - keep_prev

            # path is like b"hub/ISSUE_TEMPLATE/bug_report_cn.yaml"
            path = data[path_start:path_end].tobytes()
            # b".git" + b"hub/ISSUE_TEMPLATE/bug_report_cn.yaml"
            path = prev_path[:keep_prev] + path
            try:
                path_str = path.decode('utf-8')
            except UnicodeDecodeError as e:
                raise GitIndexBroken(f'Filepath invalid on entry {i}, path={path}, {e}')

            # Build info
            yield GitIndexEntry(*fixed_part, path_str)

            # filepath is endswith \x00, skip it
            cursor = path_end + 1
            prev_path = path

    def index_write(self, file=None, version=None):
        """
        Write .git/index file

        Args:
            file (str): Specify a file to write, None to use current file
            version (int): Specify a version to write, None to use current version
        """
        if version is None:
            version = self.version
        # prepare content
        if version in [2, 3]:
            data = b''.join(self._gen_v2())
        elif version in [4]:
            for row in self._gen_v4():
                print(row)
            data = b''.join(self._gen_v4())
        else:
            raise ValueError(f'Unsupported version: {version}')

        # add checksum
        import hashlib
        checksum = hashlib.sha1(data).digest()
        data += checksum

        if file is None:
            file = self.file
        atomic_write(file, data)

    def _gen_v2(self):
        """
        Generate data chunks of git index v2 and v3

        Yields:
            bytes:
        """
        packer = struct.Struct('>IIIIIIIIII20sH').pack
        # Entries must be sorted by path name for the index to be valid
        self.list_entry.sort(key=lambda e: e.path)

        # header
        entry_count = len(self.list_entry)
        yield struct.pack('>4sII', b'DIRC', self.version, entry_count)

        # content
        for entry in self.list_entry:
            path = entry.path.encode('utf-8')
            path_len = len(path)

            # We assume no extended flags for simplicity here.
            if path_len >= 4095:
                raise ValueError(f'Path is too long: {entry.path}')
            flags = path_len

            # Pack fixed-size metadata and path length
            yield packer(
                entry.ctime_s, entry.ctime_ns, entry.mtime_s, entry.mtime_ns,
                entry.dev, entry.ino, entry.mode, entry.uid, entry.gid, entry.size,
                entry.sha1, flags
            )
            yield path

            # align to 8 bytes
            entry_len = 62 + path_len
            pad = entry_len // 8 * 8 + 8 - entry_len
            if pad > 0:
                yield b'\x00' * pad

    def _gen_v4(self):
        """
        Generate data chunks of git index v4

        Yields:
            bytes:
        """
        packer = struct.Struct('>IIIIIIIIII20sH').pack
        # Entries must be sorted by path name for the index to be valid
        self.list_entry.sort(key=lambda e: e.path)

        # header
        entry_count = len(self.list_entry)
        yield struct.pack('>4sII', b'DIRC', self.version, entry_count)

        # content
        prev_path = b''
        for entry in self.list_entry:
            path = entry.path.encode('utf-8')
            path_len = len(path)

            # We assume no extended flags for simplicity here.
            if path_len >= 4095:
                raise ValueError(f'Path is too long: {entry.path}')
            flags = path_len

            # Pack fixed-size metadata and path length
            yield packer(
                entry.ctime_s, entry.ctime_ns, entry.mtime_s, entry.mtime_ns,
                entry.dev, entry.ino, entry.mode, entry.uid, entry.gid, entry.size,
                entry.sha1, flags
            )

            # Calculate the shared prefix length with the previous path
            prefix_len = 0
            for char1, char2 in zip(prev_path, path):
                if char1 == char2:
                    prefix_len += 1
                else:
                    break

            # N = number of bytes to remove from previous path
            n_remove = len(prev_path) - prefix_len
            # S = the suffix to append
            s_path = path[prefix_len:]

            # <varint_N><suffix_S>\x00
            if n_remove > 127:
                # 16384 - 1 at max
                yield (n_remove // 128 + 128).to_bytes(1, 'big')
                yield (n_remove % 128).to_bytes(1, 'big')
            else:
                yield n_remove.to_bytes(1, 'big')
            yield s_path
            yield b'\x00'

            prev_path = path
