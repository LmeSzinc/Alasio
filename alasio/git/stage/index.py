import struct

import msgspec

from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write
from alasio.ext.path.calc import joinnormpath
from alasio.git.stage.base import GitRepoBase


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
    path: str
    # flags
    # assume file unchanged
    assume_valid: bool = False
    # stage during merge
    stage: int = 0  # 0, 1, 2, or 3
    # extensions on version >= v3
    # for sparse checkout
    skip_worktree: bool = False
    # for `git add -N``
    intent_to_add: bool = False


# A 16-bit 'flags' field split into (high to low bits)
# 1-bit assume-valid flag
# 1-bit extended flag (must be zero in version 2)
# 2-bit stage (during merge)
# 12-bit name length if the length is less than 0xFFF; otherwise 0xFFF
# is stored in this field.
def _parse_flags(flags):
    """
    Args:
        flags (int): uint16

    Returns:
        Tuple[bool, bool, int, int]: assume_valid, extended, stage (0~3), path_len (<4096)
    """
    if flags >= 32768:
        assume_valid = True
        flags -= 32768
    else:
        assume_valid = False
    if flags >= 16384:
        extended = True
        flags -= 16384
    else:
        extended = False
    if flags >= 4096:
        stage = flags // 4096
        flags %= 4096
    else:
        stage = 0
    return assume_valid, extended, stage, flags


def _gen_flags(assume_valid, extended, stage, path_len):
    """
    Args:
        assume_valid (bool):
        extended (bool):
        stage (int): 0~3
        path_len (int): <4096

    Returns:
        int: uint16
    """
    if stage == 1 or stage == 2 or stage == 3:
        path_len += stage * 4096
    if extended:
        path_len += 16384
    if assume_valid:
        path_len += 32768
    return path_len


# (Version 3 or later) A 16-bit field, only applicable if the
# "extended flag" above is 1, split into (high to low bits).
# 1-bit reserved for future
# 1-bit skip-worktree flag (used by sparse checkout)
# 1-bit intent-to-add flag (used by "git add -N")
# 13-bit unused, must be zero
def _parse_extended_flag(flags):
    """
    Args:
        flags (int): uint16

    Returns:
        tuple[bool, bool]: skip_worktree, intent_to_add
    """
    if flags >= 32768:
        flags -= 32768
    if flags >= 16384:
        skip_worktree = True
        flags -= 16384
    else:
        skip_worktree = False
    if flags >= 8192:
        intent_to_add = True
    else:
        intent_to_add = False
    return skip_worktree, intent_to_add


def _gen_extended_flags(skip_worktree, intend_to_add):
    """
    Args:
        skip_worktree (bool):
        intend_to_add (bool):

    Returns:
        int: uint16
    """
    flags = 0
    if skip_worktree:
        flags += 16384
    if intend_to_add:
        flags += 8192
    return flags


class GitIndex(GitRepoBase):
    # all indexed files in workspace
    # key: (filepath, stage), value: GitIndexEntry object
    # stage is 0 if not in merge
    dict_entry: "dict[tuple[str, int], GitIndexEntry]" = {}
    # git index version, 2, 3, 4, or 0 if not loaded
    index_version = 0

    @cached_property
    def index_file(self):
        return joinnormpath(self.path, '.git/index')

    def clear_index_cache(self):
        self.dict_entry = {}
        self.index_version = 0

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
            file = self.index_file
        if data is None:
            data = atomic_read_bytes(file)

        try:
            signature, version, entry_count = struct.unpack('>4sII', data[:12])
        except struct.error as e:
            raise GitIndexBroken(f'Unexpected header: {data[:12]}, {e}')
        # index file must have signature DIRC
        if signature != b'DIRC':
            raise GitIndexBroken(f'Invalid signature: {signature!r}')
        self.index_version = version

        data = memoryview(data)
        if version in [2, 3]:
            self._parse_v2(data, entry_count)
        elif version in [4]:
            self._parse_v4(data, entry_count)
        else:
            # we only support v2, v3, v4
            raise GitIndexBroken(f'Unsupported version: {version}')

    def _parse_v2(self, data, entry_count):
        """
        Parse git index file of v2 and v3

        Args:
            data (memoryview):
            entry_count (int):

        Yields:
            GitIndexEntry:
        """
        dict_entry = {}
        # https://git-scm.com/docs/index-format
        unpacker = struct.Struct('>IIIIIIIIII20sH').unpack
        unpacker_uint16 = struct.Struct('>H').unpack
        # 12bytes header
        cursor = 12
        total_len = len(data)
        has_extended_flag = self.index_version >= 3

        for i in range(entry_count):
            # unpack fixed part
            path_start = cursor + 62
            try:
                fixed_part = unpacker(data[cursor:path_start])
            except struct.error:
                raise GitIndexBroken(f'File ended prematurely on entry {i} fixed part: '
                                     f'{data[cursor:path_start].hex()}')
            # ctime_s, ctime_ns, mtime_s, mtime_ns, dev, ino, mode, uid, gid, size, sha1, flags = fixed_part
            assume_valid, extended, stage, path_len = _parse_flags(fixed_part[11])

            if extended:
                if not has_extended_flag:
                    raise GitIndexBroken(
                        f'Current version {self.index_version} should not have extended_flag on entry {i}')
                try:
                    extended_flag = unpacker_uint16(data[path_start:path_start + 2])[0]
                except struct.error:
                    raise GitIndexBroken(f'File ended prematurely on entry {i} has_extended_flag: '
                                         f'{data[cursor:path_start].hex()}')
                skip_worktree, intent_to_add = _parse_extended_flag(extended_flag)
                # move on to path
                path_start += 2
            else:
                skip_worktree = False
                intent_to_add = False

            # calculate path
            if path_len == 4095:
                raise GitIndexBroken(f'Filepath length > 4095 bytes on entry {i}')
            if path_len == 0:
                raise GitIndexBroken(f'Path length is 0 on entry {i}: {fixed_part}')
            path_end = path_start + path_len
            if path_end > total_len:
                raise GitIndexBroken(f'File ended prematurely reading path for entry {i}')

            path = data[path_start:path_end].tobytes()
            try:
                path_str = path.decode('utf-8')
            except UnicodeDecodeError as e:
                raise GitIndexBroken(f'Filepath invalid on entry {i}, path={path}, {e}')

            # Build info
            dict_entry[(path_str, stage)] = GitIndexEntry(
                *fixed_part[:11], path_str,
                assume_valid=assume_valid, stage=stage, skip_worktree=skip_worktree, intent_to_add=intent_to_add)

            # calculate next cursor
            # path is aligned to 8 bytes
            if extended:
                cursor += (72 + path_len) // 8 * 8
            else:
                cursor += (70 + path_len) // 8 * 8

        self.dict_entry = dict_entry

    def _parse_v4(self, data, entry_count):
        """
        Parse git index file of v4

        Args:
            data (memoryview):
            entry_count (int):

        Yields:
            GitIndexEntry:
        """
        dict_entry = {}
        unpacker = struct.Struct('>IIIIIIIIII20sH').unpack
        unpacker_uint16 = struct.Struct('>H').unpack
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
            assume_valid, extended, stage, path_len = _parse_flags(fixed_part[11])

            if extended:
                try:
                    extended_flag = unpacker_uint16(data[path_start:path_start + 2])[0]
                except struct.error:
                    raise GitIndexBroken(f'File ended prematurely on entry {i} has_extended_flag: '
                                         f'{data[cursor:path_start].hex()}')
                skip_worktree, intent_to_add = _parse_extended_flag(extended_flag)
                # move on to path
                path_start += 2
            else:
                skip_worktree = False
                intent_to_add = False

            # calculate path
            # path_len is the final length
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
            dict_entry[(path_str, stage)] = GitIndexEntry(
                *fixed_part[:11], path_str,
                assume_valid=assume_valid, stage=stage, skip_worktree=skip_worktree, intent_to_add=intent_to_add)

            # filepath is endswith \x00, skip it
            cursor = path_end + 1
            prev_path = path

        self.dict_entry = dict_entry

    def index_write(self, file=None, version=None):
        """
        Write .git/index file

        Args:
            file (str): Specify a file to write, None to use current file
            version (int): Specify a version to write, None to use current version
        """
        if version is None:
            version = self.index_version
        # prepare content
        if version in [2, 3]:
            data = b''.join(self._gen_v2())
        elif version in [4]:
            data = b''.join(self._gen_v4())
        else:
            raise ValueError(f'Unsupported version: {version}')

        # add checksum
        import hashlib
        checksum = hashlib.sha1(data).digest()
        data += checksum

        if file is None:
            file = self.index_file
        # self.index_read(data=data)
        atomic_write(file, data)

    def _gen_v2(self):
        """
        Generate data chunks of git index v2 and v3

        Yields:
            bytes:
        """
        packer = struct.Struct('>IIIIIIIIII20sH').pack
        packer_uint16 = struct.Struct('>H').pack
        # Entries must be sorted by path name for the index to be valid
        list_entry = sorted(self.dict_entry.values(), key=lambda e: (e.path, e.stage))

        # header
        entry_count = len(list_entry)
        yield struct.pack('>4sII', b'DIRC', self.index_version, entry_count)

        # content
        for entry in list_entry:
            path = entry.path.encode('utf-8')
            path_len = len(path)

            # We assume no extended flags for simplicity here.
            if path_len >= 4095:
                raise ValueError(f'Path is too long: {entry.path}')
            extended_flags = _gen_extended_flags(entry.skip_worktree, entry.intent_to_add)
            flags = _gen_flags(entry.assume_valid, extended_flags > 0, entry.stage, path_len)

            # Pack fixed-size metadata and path length
            yield packer(
                entry.ctime_s, entry.ctime_ns, entry.mtime_s, entry.mtime_ns,
                entry.dev, entry.ino, entry.mode, entry.uid, entry.gid, entry.size,
                entry.sha1, flags
            )
            if extended_flags:
                yield packer_uint16(extended_flags)
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
        packer_uint16 = struct.Struct('>H').pack
        # Entries must be sorted by path name for the index to be valid
        list_entry = sorted(self.dict_entry.values(), key=lambda e: (e.path, e.stage))

        # header
        entry_count = len(list_entry)
        yield struct.pack('>4sII', b'DIRC', self.index_version, entry_count)

        # content
        prev_path = b''
        for entry in list_entry:
            path = entry.path.encode('utf-8')
            path_len = len(path)

            # We assume no extended flags for simplicity here.
            if path_len >= 4095:
                raise ValueError(f'Path is too long: {entry.path}')
            extended_flags = _gen_extended_flags(entry.skip_worktree, entry.intent_to_add)
            flags = _gen_flags(entry.assume_valid, extended_flags > 0, entry.stage, path_len)

            # Pack fixed-size metadata and path length
            yield packer(
                entry.ctime_s, entry.ctime_ns, entry.mtime_s, entry.mtime_ns,
                entry.dev, entry.ino, entry.mode, entry.uid, entry.gid, entry.size,
                entry.sha1, flags
            )
            if extended_flags:
                yield packer_uint16(extended_flags)

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
