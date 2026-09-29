import os
from collections import defaultdict
from threading import Lock

from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.ext.path.calc import joinnormpath
from alasio.git.file.exception import PackBroken
from alasio.git.file.loose import LoosePath
from alasio.git.file.pack import PackFile
from alasio.git.obj.obj import OBJTYPE_BASIC, GitLooseObject, GitObject, parse_objdata
from alasio.git.stage.base import GitRepoBase


class GitObjectManager(GitRepoBase):
    def __init__(self, path):
        """
        Args:
            path (str): Absolute path to repo, repo should contain .git folder
        """
        super().__init__(path)
        # one lock per sha1 that is being built, created on demand and removed
        # when the build is done, see cat_shallow()
        self.cat_create_lock = Lock()
        self.cat_locks: "dict[str, Lock]" = {}

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
        loose = THREAD_POOL.start_thread_soon(self.loose.loose_read_lazy)
        # read pack and idx
        with THREAD_POOL.wait_jobs() as pool:
            for pack in self.dict_pack.values():
                pool.start_thread_soon(pack.read_full)
        # get loose result
        loose.get()

        self._manager_build()
        self._manager_clear_sub()
        return self

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
        loose = THREAD_POOL.start_thread_soon(self.loose.loose_read_lazy)
        # read pack and idx
        with THREAD_POOL.wait_jobs() as pool:
            for pack in self.dict_pack.values():
                pool.start_thread_soon(pack.read_lazy, skip_size)
        # get loose result
        loose.get()

        self._manager_build()
        self._manager_clear_sub()
        return self

    def _cat_publish(self, sha1, obj):
        """
        Finish an object and publish it into the object dict.

        The object dict is shared by every thread that calls cat(), so an
        object is decoded before it is published: a thread that finds an object
        in the dict sees a complete one (type, data and decoded agree), and a
        published object is never modified afterwards. Without that, a second
        thread catches an object in the middle of its lazy decode and reads the
        delta instructions as if they were the object content.

        Args:
            sha1 (str):
            obj (GitObject | GitLooseObject):
        """
        # decoded() turns data from packed bytes into plain ones for a basic
        # object, and parses the delta header of a delta object, see cat()
        obj.decoded
        self.dict_object[sha1] = obj

    def cat_shallow(self, sha1):
        """
        Get object from given sha1.

        A finished object is found without any lock. A missing object is built
        under the lock of its own sha1, so two threads that build different
        objects never wait for each other and one object is built once.

        Args:
            sha1 (str):

        Returns:
            GitObject | GitLooseObject:

        Raises:
            KeyError: If sha1 not exists
            PackBroken:
            ObjectBroken:
        """
        # fast path, a built object is returned without any lock
        obj = self.dict_object.get(sha1)
        if obj is not None:
            return obj

        # create pre-object lock
        with self.cat_create_lock:
            if sha1 in self.cat_locks:
                try:
                    lock = self.cat_locks[sha1]
                except KeyError:
                    # race condition
                    lock = Lock()
                    self.cat_locks[sha1] = lock
            else:
                lock = Lock()
                self.cat_locks[sha1] = lock

        with lock:
            # double-checked locking
            # check if the object was built before the lock was acquired
            obj = self.dict_object.get(sha1)
            if obj is not None:
                # remove pre-object lock to reduce memory
                try:
                    del self.cat_locks[sha1]
                except KeyError:
                    pass
                return obj

            # build
            try:
                obj = self._cat_build(sha1)
            finally:
                # remove pre-object lock to reduce memory
                # also on exception, otherwise the lock table keeps one entry per sha1 that failed once
                try:
                    del self.cat_locks[sha1]
                except KeyError:
                    pass
            return obj

    def _cat_build(self, sha1):
        """
        Build one object of a sha1 that is in no object dict, and publish it.

        Internal: the caller holds the lock of the sha1, see cat_shallow()

        Args:
            sha1 (str):

        Returns:
            GitObject | GitLooseObject:

        Raises:
            KeyError: If sha1 not exists
            PackBroken:
            ObjectBroken:
        """
        # data -> obj
        dict_object_data = self.dict_object_data
        data = dict_object_data.get(sha1)
        if data is not None:
            obj = parse_objdata(data)
            self._cat_publish(sha1, obj)
            try:
                del dict_object_data[sha1]
            except KeyError:
                # may be deleted by another thread
                pass
            return obj

        # read file -> data -> obj
        try:
            file = self.dict_object_unread[sha1]
        except KeyError:
            # the object may be built and its two sources consumed by another
            # thread between the lookups above, a stale cache miss
            obj = self.dict_object.get(sha1)
            if obj is not None:
                return obj
            # Not found
            raise KeyError(f'No such object sha1={sha1}')
        obj = file.addread(sha1)
        self._cat_publish(sha1, obj)
        try:
            del self.dict_object_unread[sha1]
        except KeyError:
            # may be deleted by another thread
            pass
        return obj

    def cat(self, sha1):
        """
        Get object from given sha1, and recursively solve delta objects

        cat() is thread safe: read_lazy() or read_full() replaces the object
        dicts and must have finished, then any number of threads can call
        cat(). The object dict only holds finished objects and a finished
        object is never modified: a delta chain is solved into new objects, and
        every solved object is published when it is done, so two threads that
        solve the same chain both get a complete object. A chain that two
        threads solve at the same time is solved twice, which costs them one
        cache miss each.

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
        # notes:
        # don't use recursion to handle delta objects
        # because delta reference can up to depth of 4096 and python can only have recursion depth < 1000
        # chain is (sha1, object) of every object of the chain, the requested
        # object first and its base object last, all of them read only
        dict_object_from = self.dict_object_from
        chain = [(sha1, obj)]
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
            elif typ == 7:
                # sha1 -> ref sha1
                sha1 = obj.decoded.ref
            else:
                # non-delta object
                break
            obj = self.cat_shallow(sha1)
            chain.append((sha1, obj))

        # apply delta
        # chain is (source, delta, delta, ...)
        # the base object is the last of the chain, every step builds a new
        # object out of the delta and the object it is based on, the delta
        # objects of the chain are left untouched
        source = chain[-1][1]
        for delta_sha1, delta in reversed(chain[:-1]):
            source = delta.resolved_from(source)
            # publish the solved object of the delta, the whole chain is
            # solved and its objects are warmed like before
            self._cat_publish(delta_sha1, source)

        # the requested object is the first of the chain, the last one solved
        return source
