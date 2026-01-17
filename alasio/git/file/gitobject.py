import os
from collections import defaultdict, deque

from alasio.ext.path.calc import joinnormpath
from alasio.ext.pool import WORKER_POOL
from alasio.git.file.exception import PackBroken
from alasio.git.file.loose import LoosePath
from alasio.git.file.pack import PackFile
from alasio.git.obj.obj import GitLooseObject, GitObject, OBJTYPE_BASIC, parse_objdata
from alasio.git.stage.base import GitRepoBase


class GitObjectManager(GitRepoBase):
    # key: filepath to pack file, value: PackFile object
    dict_pack: "dict[os.DirEntry, PackFile]" = {}
    # LoosePath object to manage loose files
    loose: LoosePath = None

    # all git objects
    # key: sha1 of git object, value: GitObject
    dict_object: "dict[str, GitObject | GitLooseObject]" = {}
    # git objects that not yet parsed
    # key: sha1 of git object, value: data in memoryview
    dict_object_data: "dict[str, memoryview]" = {}
    # git objects that not yet read
    # key: sha1 of git object, value: self
    dict_object_unread: "dict[str, PackFile | LoosePath]" = {}
    # where git object is from, used for query ofs_delta
    # key: sha1 of git object, value: sub manager
    dict_object_from: "dict[str, PackFile | LoosePath]" = {}

    # Skip reading objects with size > skip_size in lazy read
    # 1MB is balanced value that assume reading from HDD of 100MB/s read and 100 IOPS,
    # so read 1MB less file read means we can have 1 more file seek
    skip_size: int = 1048576

    def _manager_prepare(self):
        """
        Prepare sub managers
        """
        dict_pack = {}
        for pack, _ in self._iter_pack_idx():
            dict_pack[pack] = PackFile(pack.path)
        self.loose = LoosePath(joinnormpath(self.path, '.git/objects'))
        self.dict_pack = dict_pack

    def _iter_pack_idx(self):
        """
        Iter .pack and .idx file pair

        Yields:
            tuple[DirEntry, DirEntry]: A pair of .pack file and .idx file
        """
        path = joinnormpath(self.path, '.git/objects/pack')
        try:
            list_entry = list(os.scandir(path))
        except (FileNotFoundError, NotADirectoryError):
            # path not exist, no files
            return

        # sort by mtime ascending
        # if multiple pack files contain the same object, the newer one will be used
        dict_mtime = {}
        for entry in list_entry:
            try:
                if not entry.is_file():
                    continue
                stat = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            dict_mtime[entry] = stat.st_mtime
        list_entry = sorted(dict_mtime.items(), key=lambda item: item[1])

        # pair files
        file_candidates = defaultdict(dict)
        for entry, _ in list_entry:
            name, _, suffix = entry.name.rpartition('.')
            file_candidates[name][suffix] = entry
        for name, files in file_candidates.items():
            try:
                pack = files['pack']
                idx = files['idx']
            except KeyError:
                continue
            yield pack, idx

    def _manager_build(self):
        """
        Build object dict from sub managers
        """
        # we prefer pack files to be the final data, because it does not need extra read
        # this is different from git
        dict_object: "dict[str, GitObject | GitLooseObject]" = self.loose.dict_object
        dict_object_data: "dict[str, memoryview]" = self.loose.dict_object_data
        dict_object_unread: "dict[str, PackFile | LoosePath]" = self.loose.dict_object_unread
        dict_object_from: "dict[str, PackFile | LoosePath]" = dict.fromkeys(self.loose.dict_object_unread, self.loose)

        # if multiple pack files contain the same object, the newer one will be used
        for pack in self.dict_pack.values():
            dict_object.update(pack.dict_object)
            dict_object_data.update(pack.dict_object_data)
            dict_object_unread.update(pack.dict_object_unread)
            object_from = dict.fromkeys(pack.dict_offset, pack)
            dict_object_from.update(object_from)

        self.dict_object = dict_object
        self.dict_object_data = dict_object_data
        self.dict_object_unread = dict_object_unread
        self.dict_object_from = dict_object_from

    def _manager_clear_sub(self):
        """
        Clear object dict from sub managers to release memory
        """
        for pack in self.dict_pack.values():
            pack.clear_object()
        self.loose.clear_object()

    def read_full(self):
        """
        Read all pack files and loose objects.
        This may use a lot of RAM if your git repo is big
        """
        self._manager_prepare()

        # read loose but get result very later
        loose = WORKER_POOL.start_thread_soon(self.loose.loose_read_lazy)
        # read pack and idx
        with WORKER_POOL.wait_jobs() as pool:
            for pack in self.dict_pack.values():
                pool.start_thread_soon(pack.read_full)
        # get loose result
        loose.get()

        self._manager_build()
        self._manager_clear_sub()

    def read_lazy(self, skip_size=None):
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

        self._manager_prepare()

        # read loose but get result very later
        loose = WORKER_POOL.start_thread_soon(self.loose.loose_read_lazy)
        # read pack and idx
        with WORKER_POOL.wait_jobs() as pool:
            for pack in self.dict_pack.values():
                pool.start_thread_soon(pack.read_lazy, skip_size)
        # get loose result
        loose.get()

        self._manager_build()
        self._manager_clear_sub()

    def cat_shallow(self, sha1):
        """
        Get object from given sha1.

        Args:
            sha1 (str):

        Returns:
            GitObject | GitLooseObject:

        Raises:
            KeyError: If sha1 not exists
            PackBroken:
            ObjectBroken:
        """
        # existing object
        dict_object = self.dict_object
        obj = dict_object.get(sha1)
        if obj is not None:
            return obj

        # data -> obj
        dict_object_data = self.dict_object_data
        data = dict_object_data.get(sha1)
        if data is not None:
            obj = parse_objdata(data)
            dict_object[sha1] = obj
            try:
                del dict_object_data[sha1]
            except KeyError:
                # may be deleted by another thread
                pass
            return obj

        # read file -> data -> obj
        dict_object_unread = self.dict_object_unread
        try:
            file = dict_object_unread[sha1]
        except KeyError:
            # this shouldn't happen
            pass
        else:
            obj = file.addread(sha1)
            dict_object[sha1] = obj
            try:
                del dict_object_unread[sha1]
            except KeyError:
                # may be deleted by another thread
                pass
            return obj
        # Not found
        raise KeyError(f'No such object sha1={sha1}')

    def cat(self, sha1):
        """
        Get object from given sha1, and recursively solve delta objects

        Args:
            sha1 (str):

        Returns:
            GitObject | GitLooseObject:

        Raises:
            KeyError: If sha1 not exists
            PackBroken:
            ObjectBroken:
        """
        obj = self.cat_shallow(sha1)
        if obj.type in OBJTYPE_BASIC:
            return obj

        # lookup delta
        dict_object_from = self.dict_object_from
        result_obj = obj
        # notes:
        # don't use recursion to handle delta objects
        # because delta reference can up to depth of 4096 and python can only have recursion depth < 1000
        queue = deque([obj])
        while 1:
            typ = obj.type
            if typ == 6:
                # sha1 -> source sha1
                offset_delta = obj.decoded.offset
                try:
                    pack = dict_object_from[sha1]
                except KeyError:
                    # this should not happen
                    raise PackBroken(f'Failed to solve ofs_delta object {sha1}: cannot find where it came from')
                try:
                    offset_base = pack.dict_offset[sha1][0]
                except KeyError:
                    # this should not happen
                    raise PackBroken(f'Failed to solve ofs_delta object {sha1}: cannot find its offset')
                offset = offset_base - offset_delta
                if offset < 0:
                    # this should not happen
                    raise PackBroken(f'Failed to solve ofs_delta object {sha1}: source offset {offset} < 0')
                try:
                    sha1 = pack.dict_offset_to_sha1[offset]
                except KeyError:
                    # this should not happen
                    raise PackBroken(f'Failed to solve ofs_delta object {sha1}: '
                                     f'offset {offset} does not point to any object in {pack.pack_file}')
                obj = self.cat_shallow(sha1)
                queue.appendleft(obj)
            elif typ == 7:
                # sha1 -> ref sha1
                sha1 = obj.decoded.ref
                obj = self.cat_shallow(sha1)
                queue.appendleft(obj)
            else:
                # non-delta object
                break

        # apply delta
        # queue is (source, delta, delta, ...)
        source = queue.popleft()
        _ = source.decoded
        for delta in queue:
            delta.apply_delta_from_source(source)
            source = delta

        # result_obj is the last object of queue and obj.decoded should have be set
        return result_obj
