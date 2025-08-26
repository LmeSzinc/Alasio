import hashlib
import os
import struct

from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.path.calc import with_suffix
from alasio.git.file.exception import PackBroken


class IdxFile:
    def __init__(self, file):
        """
        Parse .idx file of git with python directly
        https://shafiul.github.io/gitbook/7_the_packfile.html

        Args:
            file (str): Path to .pack file or .idx file
        """
        if file.endswith('.idx'):
            self.pack_file = with_suffix(file, '.pack')
            self.idx_file = file
        elif file.endswith('.pack'):
            self.pack_file = file
            self.idx_file = with_suffix(file, '.idx')
        else:
            self.pack_file = file + '.pack'
            self.idx_file = file + '.idx'

        # key: sha1 length=40, value: (offset_start, offset_end)
        self.dict_offset: "dict[str, tuple[int, int]]" = {}
        self.dict_offset_to_sha1: "dict[int, str]" = {}
        # end of data in pack file, checksum sha1 not included
        self.pack_end: int = 0
        # last modify time of idx file
        self.mtime: float = 0.
        self.pack_sha1: str = ''
        self.idx_sha1: str = ''

    def clear_idx(self):
        self.dict_offset.clear()
        self.dict_offset_to_sha1.clear()
        self.pack_end = 0
        self.mtime = 0.
        self.pack_sha1 = ''
        self.idx_sha1 = ''

    def idx_read(self):
        """
        Read .idx file and parse it

        Returns:
            int: Amount of hashes in idx file

        Raises:
            FileNotFoundError:
            PackBroken:
        """
        # Get pack size
        pack_size = os.stat(self.pack_file).st_size
        # the end of objects, last 20 bytes is sha1 of all object sha1
        pack_end = pack_size - 20
        if pack_end <= 0:
            raise PackBroken(f'Pack file too short: {pack_size}')

        # Get idx mtime
        mtime = os.stat(self.idx_file).st_mtime

        # Read
        data = atomic_read_bytes(self.idx_file)
        # Version 2 pack index
        if not data.startswith(b'\xfftOc\x00\x00\x00\x02'):
            raise PackBroken(f'Unexpected idx header {data[:8]}')
        data = memoryview(data)

        # Since we are in python and python has dict, we don't need fanout to lookup sha1
        # fanout_table = data[8:1032]

        # Get size, last fanout is size
        size = data[1028:1032]
        if not size:
            raise PackBroken(f'Empty idx size')
        try:
            size = struct.unpack('>I', size)[0]
        except struct.error as e:
            raise PackBroken(str(e))

        # sha1 table
        end = 1032 + size * 20
        table = data[1032: end]
        if not table:
            raise PackBroken(f'Empty sha1 table')
        length = len(table)
        if length % 20:
            raise PackBroken('sha1 table length is not multiple of 20')
        # extract memoryview to release the entire file data
        chunks = length // 20
        try:
            sha1_list = struct.unpack('20s' * chunks, table)
        except struct.error as e:
            raise PackBroken(str(e))
        sha1_list = [sha1.hex() for sha1 in sha1_list]

        # crc table
        # well, we don't need crc
        # crc_table = data[end: end + size * 4]

        # offset table
        start = end + size * 4
        end = start + size * 4
        table = data[start:end]
        if not table:
            raise PackBroken(f'Empty offset table')
        length = len(table)
        if length % 4:
            raise PackBroken('Length of offset table is not multiple of 4')
        try:
            offset_list = struct.unpack(f'>{length // 4}I', table)
        except struct.error as e:
            raise PackBroken(str(e))

        # large table offset
        large_size = 0
        for i in offset_list:
            if i >= 2147483648:
                large_size += 1
        if large_size:
            start = end
            expect_length = large_size * 8
            end = start + expect_length
            table = data[start:end]
            if not table:
                raise PackBroken(f'Empty large offset table')
            length = len(table)
            if length % 8:
                raise PackBroken('Length of large offset table is not multiple of 8')
            if length != expect_length:
                raise PackBroken('Length of large offset table does not match large size')
            try:
                large_list = struct.unpack(f'>{length // 8}Q', table)
            except struct.error as e:
                raise PackBroken(str(e))
            try:
                offset_list = [large_list[i - 2147483648] if i >= 2147483648 else i for i in offset_list]
            except IndexError:
                raise PackBroken('IndexError when query large offset')

        # trailer sha1
        pack_sha1 = data[end:end + 20]
        if len(pack_sha1) != 20:
            raise PackBroken(f'Unexpected length of pack sha1: {pack_sha1}')
        idx_sha1 = data[end + 20:end + 40]
        if len(idx_sha1) != 20:
            raise PackBroken(f'Unexpected length of idx sha1: {idx_sha1}')
        # extract memoryview to release the entire file data
        pack_sha1 = pack_sha1.hex()
        idx_sha1 = idx_sha1.hex()
        # validate sha1
        sha1 = hashlib.sha1(data[:end + 20]).hexdigest()
        if sha1 != idx_sha1:
            raise PackBroken(f'Idx file sha1 not match: current={sha1}, expected={idx_sha1}')

        # end of file
        rest = data[end + 40:]
        if rest:
            raise PackBroken(f'Idx file read end but {len(rest)} bytes left')
        if len(sha1_list) != len(offset_list):
            raise PackBroken(f'Length not match, sha1_list={len(sha1_list)}, offset_list={len(offset_list)}')

        # result
        dict_offset = {}
        dict_offset_to_sha1 = dict(zip(offset_list, sha1_list))
        offset_list = sorted(zip(sha1_list, offset_list), key=lambda x: x[1])
        prev_offset = None
        prev_sha1 = None
        for sha1, offset in offset_list:
            if prev_offset is not None:
                dict_offset[prev_sha1] = (prev_offset, offset)
            prev_offset = offset
            prev_sha1 = sha1
        dict_offset[prev_sha1] = (prev_offset, pack_end)

        # set attribute
        self.dict_offset = dict_offset
        self.dict_offset_to_sha1 = dict_offset_to_sha1
        self.pack_end = pack_end
        self.mtime = mtime
        self.pack_sha1 = pack_sha1
        self.idx_sha1 = idx_sha1
