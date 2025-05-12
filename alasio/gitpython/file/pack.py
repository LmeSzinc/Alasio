from typing import Tuple

from alasio.ext.path.atomic import atomic_read_bytes
from alasio.gitpython.file.exception import PackBroken
from alasio.gitpython.file.idx import IdxFile
from alasio.gitpython.obj.obj import GitObject, parse_objdata


class PackFile(IdxFile):
    def __init__(self, file):
        """
        Parse .pack file of git with python directly
        https://shafiul.github.io/gitbook/7_the_packfile.html

        Args:
            file (str): Path to .pack file or .idx file
        """
        super().__init__(file)

        # all git objects that read
        # key: sha1 of git object, value: GitObject
        self.dict_object: "dict[str, GitObject]" = {}
        # git objects that didn't read, will be set in pack_read_split()
        # key: sha1 of git object, value: GitObject
        self.dict_object_lazy: "dict[str, GitObject]" = {}

    def pack_read_full(self):
        """
        Read the entire .pack file and parse it.
        self.idx_read() needs to be called first

        Raises:
            FileNotFoundError:
            PackFileTruncated:
        """
        if not self.dict_offset:
            return
        data = atomic_read_bytes(self.pack_file)
        data = memoryview(data)
        # the end of objects, last 20 bytes is sha1 of all object sha1
        object_end = len(data) - 20
        if object_end <= 0:
            raise PackBroken(f'Pack file too short: {len(data)}')

        # read by .idx file
        dict_object = {}
        for sha1, offset in self.dict_offset.items():
            offset_start, offset_end = offset
            if offset_end > object_end:
                raise PackBroken(f'offset {offset_start} to {offset_end} is out of pack size {len(data)}')
            object_data = data[offset_start:offset_end]
            obj = parse_objdata(object_data)
            dict_object[sha1] = obj

        # set attribute
        self.dict_object = dict_object
        self.dict_object_lazy = {}

    def _pack_iter_lazy_segment(self, skip_size=1048576):
        """
        Iter segment info to read in pack file

        Args:
            skip_size (int):

        Returns:
            Tuple[int, int, list[Tuple[str, int, int]]]:
                segment_start, segment_size, segment
                    where segment is a list if (sha1, offset_start, offset_end)
        """
        # notes:
        # 12 is the header size of pack file
        # 10 bytes is the maximum length of object header with 64bit object size,
        #   (10 bytes can contain 4 + 9 * 7 = 67bit of size)
        segment = []
        segment_start = -1
        offset_end = 0
        for sha1, offset in self.dict_offset.items():
            offset_start, offset_end = offset
            data_length = offset_end - offset_start
            if data_length <= 0:
                # This shouldn't happen
                continue

            if segment_start < 0:
                # first object in segment
                if offset_start <= 12:
                    # read pack header to reduce seek calls
                    segment_start = 0
                else:
                    # start object of latter segment
                    segment_start = offset_start
                    offset_start = 0
                    offset_end = data_length
            else:
                # letter objects, offset starts from segment start
                offset_start -= segment_start
                offset_end -= segment_start

            if data_length > skip_size:
                # big object, read first 10 bytes
                offset_end = offset_start + 10
                segment.append((sha1, offset_start, offset_end))
                # yield segment
                yield segment_start, offset_end, segment
                segment = []
                segment_start = -1
            else:
                # normal object
                segment.append((sha1, offset_start, offset_end))

        # yield last segment
        yield segment_start, offset_end, segment

    def pack_read_lazy(self, skip_size=1048576):
        """
        Read pack file but skip objects that size > skip_size
        if object skipped, object will be set into dict_object_lazy
        otherwise, object will be set into dict_object

        Args:
            skip_size (int): Default to 1MB.
                1MB is balanced value that assume reading from HDD of 100MB/s read and 100 IOPS,
                so read 1MB less file read means we can have 1 more file seek
        """
        dict_object = {}
        dict_object_lazy = {}
        with open(self.pack_file, 'rb') as f:
            # iter segment info, read by segment, slice segment into objects
            for offset, size, segment in self._pack_iter_lazy_segment(skip_size):
                # skip seeking offset=0, just direct read
                if offset > 0:
                    f.seek(offset)
                data = f.read(size)
                data = memoryview(data)
                # read normal objects
                for sha1, start, end in segment[:-1]:
                    object_data = data[start:end]
                    obj = parse_objdata(object_data)
                    dict_object[sha1] = obj
                # read last object, the big object to skip reading
                sha1, start, end = segment[-1]
                object_data = data[start:end]
                obj = parse_objdata(object_data)
                if end + offset >= self.pack_end:
                    # object reached end, still normal object
                    dict_object[sha1] = obj
                else:
                    # big object, mark as lazy read
                    dict_object_lazy[sha1] = obj

        # set attribute
        self.dict_object = dict_object
        self.dict_object_lazy = dict_object_lazy
