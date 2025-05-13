import os
from collections import defaultdict
from os import DirEntry

from alasio.ext.path.calc import joinnormpath
from alasio.ext.pool import WORKER_POOL
from alasio.gitpython.file.loose import LoosePath
from alasio.gitpython.file.pack import PackFile
from alasio.gitpython.obj.obj import GitLooseObject, GitObject, parse_objdata


class GitObjectManager:
    def __init__(self, path):
        """
        Args:
            path (str): Absolute path to repo, repo should contain .git folder
        """
        self.path: str = path

        # key: filepath to pack file, value: PackFile object
        self.dict_pack: "dict[DirEntry, PackFile]" = {}
        # LoosePath object to manage loose files
        self.loose: LoosePath = None

        # all git objects
        # key: sha1 of git object, value: GitObject
        self.dict_object: "dict[str, GitObject | GitLooseObject]" = {}
        # git objects that not yet parsed
        # key: sha1 of git object, value: data in memoryview
        self.dict_object_data: "dict[str, memoryview]" = {}
        # git objects that not yet read
        # key: sha1 of git object, value: self
        self.dict_object_unread: "dict[str, PackFile | LoosePath]" = {}

        # Skip reading objects with size > skip_size in lazy read
        # 1MB is balanced value that assume reading from HDD of 100MB/s read and 100 IOPS,
        # so read 1MB less file read means we can have 1 more file seek
        self.skip_size: int = 1048576

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
        dict_object = {}
        dict_object_data = {}
        dict_object_unread = {}

        # if multiple pack files contain the same object, the newer one will be used
        for pack in self.dict_pack.values():
            dict_object.update(pack.dict_object)
            dict_object_data.update(pack.dict_object_data)
            dict_object_unread.update(pack.dict_object_unread)

        # if loose object exists, use loose object first
        dict_object.update(self.loose.dict_object)
        # dict_object_data.update(self.loose.dict_object_data)
        dict_object_unread.update(self.loose.dict_object_unread)

        self.dict_object = dict_object
        self.dict_object_data = dict_object_data
        self.dict_object_unread = dict_object_unread

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
        # read idx
        with WORKER_POOL.wait_jobs() as pool:
            for pack in self.dict_pack.values():
                pool.start_thread_soon(pack.idx_read)
        # read pack
        with WORKER_POOL.wait_jobs() as pool:
            for pack in self.dict_pack.values():
                pool.start_thread_soon(pack.pack_read_full)
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
        # read idx
        with WORKER_POOL.wait_jobs() as pool:
            for pack in self.dict_pack.values():
                pool.start_thread_soon(pack.idx_read)
        # read pack
        with WORKER_POOL.wait_jobs() as pool:
            for pack in self.dict_pack.values():
                pool.start_thread_soon(pack.pack_read_lazy, skip_size)
        # get loose result
        loose.get()

        self._manager_build()
        self._manager_clear_sub()

    def cat(self, sha1):
        """
        Get object from given sha1.
        for small use only:
            self.cat('032b77768b275533da25d10ed87a7d3fbb9f7975')
        using in large scale would result in bad performance:
            for sha1 in ...:
                obj = self.cat(sha1)

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
        try:
            return self.dict_object[sha1]
        except KeyError:
            pass
        # data -> obj
        dict_object_data = self.dict_object_data
        try:
            data = dict_object_data[sha1]
        except KeyError:
            pass
        else:
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
