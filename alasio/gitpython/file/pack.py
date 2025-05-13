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

        # all git objects
        # key: sha1 of git object, value: GitObject
        self.dict_object: "dict[str, GitObject]" = {}
        # git objects that not yet parsed
        # key: sha1 of git object, value: data in memoryview
        self.dict_object_data: "dict[str, memoryview]" = {}
        # git objects that not yet read
        # key: sha1 of git object, value: self
        self.dict_object_unread: "dict[str, PackFile]" = {}
        # Skip reading objects with size > skip_size in lazy read
        # 1MB is balanced value that assume reading from HDD of 100MB/s read and 100 IOPS,
        # so read 1MB less file read means we can have 1 more file seek
        self.skip_size: int = 1048576

    def clear_object(self):
        self.dict_object = {}
        self.dict_object_data = {}
        self.dict_object_lazy = {}

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
        dict_object_data = {}
        for sha1, offset in self.dict_offset.items():
            offset_start, offset_end = offset
            if offset_end > object_end:
                raise PackBroken(f'offset {offset_start} to {offset_end} is out of pack size {len(data)}')
            object_data = data[offset_start:offset_end]
            dict_object_data[sha1] = object_data

        # set attribute
        self.dict_object = {}
        self.dict_object_data = dict_object_data
        self.dict_object_unread = {}

    def _pack_iter_lazy_segment(self, dict_offset=None, skip_size=None):
        """
        Iter segment info for lazy reading

        Args:
            dict_offset (dict):
            skip_size (int):

        Returns:
            Tuple[int, int, list[Tuple[str, int, int]]]:
                segment_start, segment_size, segment
                    where segment is a list if (sha1, offset_start, offset_end)
        """
        if dict_offset is None:
            dict_offset = self.dict_offset
        if skip_size is None:
            skip_size = self.skip_size
        # notes:
        # 10 bytes is the maximum length of object header with 64bit object size,
        #   (10 bytes can contain 4 + 9 * 7 = 67bit of size)
        segment = []
        segment_start = -1
        offset_end = 0
        for sha1, offset in dict_offset.items():
            offset_start, offset_end = offset
            data_length = offset_end - offset_start
            if data_length <= 0:
                # This shouldn't happen
                continue

            if segment_start < 0:
                # first object in segment
                if offset_start <= skip_size:
                    # if first offset < skip_size, read all before to reduce 1 seek call
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
                # new segment
                segment = []
                segment_start = -1
            else:
                # normal object
                segment.append((sha1, offset_start, offset_end))

        # yield last segment
        if segment_start >= 0:
            yield segment_start, offset_end, segment

    def _pack_iter_read_segment(self, dict_offset=None, skip_size=None):
        """
        Iter segment info to read specific offset

        Args:
            dict_offset (dict):
            skip_size (int):

        Returns:
            Tuple[int, int, list[Tuple[str, int, int]]]:
                segment_start, segment_size, segment
                    where segment is a list if (sha1, offset_start, offset_end)
        """
        if dict_offset is None:
            dict_offset = self.dict_offset
        if skip_size is None:
            skip_size = self.skip_size

        segment = []
        segment_start = -1
        offset_end = 0
        prev_end = 0
        for sha1, offset in dict_offset.items():
            offset_start, offset_end = offset
            data_length = offset_end - offset_start
            if data_length <= 0:
                # This shouldn't happen
                continue

            if segment_start < 0:
                # first object in segment
                if offset_start <= skip_size:
                    # if first offset < skip_size, read all before to reduce 1 seek call
                    segment_start = 0
                else:
                    # start object of latter segment
                    segment_start = offset_start
                    offset_start = 0
                    offset_end = data_length
                prev_end = offset_end
            else:
                # letter objects, offset starts from segment start
                offset_start -= segment_start
                offset_end -= segment_start

            if offset_start - prev_end > skip_size:
                # big gap between 2 objects, treat as 2 segment
                # yield segment
                yield segment_start, offset_end, segment
                # new segment
                segment = [(sha1, 0, data_length)]
                segment_start = offset_start
                offset_end = data_length
                prev_end = data_length
            else:
                # normal object
                segment.append((sha1, offset_start, offset_end))
                prev_end = offset_end

        # yield last segment
        if segment_start >= 0:
            yield segment_start, offset_end, segment

    def pack_read_lazy(self, skip_size=None):
        """
        Read pack file but skip objects that size > skip_size
        if object skipped, object will be set into dict_object_lazy
        otherwise, object will be set into dict_object

        Args:
            skip_size (int): Default to 1MB.
                1MB is balanced value that assume reading from HDD of 100MB/s read and 100 IOPS,
                so read 1MB less file read means we can have 1 more file seek
        """
        if skip_size is None:
            skip_size = self.skip_size

        dict_object_data = {}
        dict_object_unread = {}
        with open(self.pack_file, 'rb', buffering=0) as f:
            # iter segment info, read by segment, slice segment into objects
            for offset, size, segment in self._pack_iter_lazy_segment(skip_size=skip_size):
                # skip seeking offset=0, just direct read
                if offset > 0:
                    f.seek(offset)
                data = f.read(size)
                data = memoryview(data)
                # read normal objects
                for sha1, start, end in segment[:-1]:
                    object_data = data[start:end]
                    dict_object_data[sha1] = object_data
                # read last object, the big object to skip reading
                sha1, start, end = segment[-1]
                object_data = data[start:end]
                if end + offset >= self.pack_end:
                    # object reached end, still normal object
                    dict_object_data[sha1] = object_data
                else:
                    # big object, mark as lazy read
                    dict_object_unread[sha1] = self

        # set attribute
        self.dict_object = {}
        self.dict_object_data = dict_object_data
        self.dict_object_unread = dict_object_unread

    def addread(self, sha1, skip_size=None):
        """
        Re-read git objects
        To read multiple lazy loaded objects, input a list of sha1 instead of calling pack_addread() multiple times

        Args:
            sha1 (list[str], str): sha1 or a list of sha1

        Returns:
            dict[str, GitObject] | GitObject:
        """
        if skip_size is None:
            skip_size = self.skip_size

        if isinstance(sha1, list):
            dict_offset = {}
            old_offset = self.dict_offset
            for sha in sha1:
                try:
                    offset = old_offset[sha]
                except KeyError:
                    raise PackBroken(f'Missing pack object offset sha1={sha}')
                dict_offset[sha] = offset
            # read
            dict_object = {}
            with open(self.pack_file, 'rb', buffering=0) as f:
                # iter segment info, read by segment, slice segment into objects
                for offset, size, segment in self._pack_iter_lazy_segment(dict_offset, skip_size=skip_size):
                    # skip seeking offset=0, just direct read
                    if offset > 0:
                        f.seek(offset)
                    data = f.read(size)
                    data = memoryview(data)
                    # read normal objects
                    for sha1, start, end in segment:
                        object_data = data[start:end]
                        obj = parse_objdata(object_data)
                        dict_object[sha1] = obj
            # set attribute
            # self.dict_object_data.update(dict_object_data)
            # for sha1 in dict_object_data:
            #     self.dict_object_unread.pop(sha1)
            return dict_object
        else:
            try:
                offset_start, offset_end = self.dict_offset[sha1]
            except KeyError:
                raise PackBroken(f'Missing pack object offset sha1={sha1}')
            # read
            with open(self.pack_file, 'rb', buffering=0) as f:
                if offset_start <= skip_size:
                    # reading file head, reduce 1 seek call
                    object_data = f.read(offset_end)
                    object_data = memoryview(object_data[offset_start:])
                else:
                    # normal file, seek and read
                    f.seek(offset_start)
                    size = offset_end - offset_start
                    object_data = f.read(size)
                    object_data = memoryview(object_data)

            obj = parse_objdata(object_data)
            # set attribute
            # self.dict_object_data[sha1] = object_data
            # self.dict_object_unread.pop(sha1, None)
            return obj
