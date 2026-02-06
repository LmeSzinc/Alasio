import itertools

from alasio.ext.cache import cached_property
from alasio.ext.concurrent.threadpool import THREAD_POOL
from alasio.git.file.gitobject import GitObjectManager
from alasio.git.file.loose import LoosePath
from alasio.git.obj.obj import OBJTYPE_BASIC, parse_objdata
from alasio.git.obj.objdelta import parse_ofs_delta_offset, parse_ref_delta_ref
from alasio.logger import logger


def batch_generator(iterable, batch_size=50):
    """
    Args:
        iterable: e.g., list, tuple, dict.items()
        batch_size (int):

    Yields:
        list: 代表一个批次的列表。
    """
    # 从可迭代对象创建一个迭代器
    it = iter(iterable)
    while True:
        batch = list(itertools.islice(it, batch_size))
        if not batch:
            break
        yield batch


class GitCommit(GitObjectManager):
    @cached_property
    def dict_objtype(self):
        """
        Get the final object type for all objects in the repository.
        Resolves delta objects to their base types (1,2,3,4).

        Returns:
            dict[str, int]: SHA1 to object type mapping
                type 1: commit
                type 2: tree
                type 3: blob
                type 4: tag
        """
        result = {}
        # key: ref_from, value: ref_to
        delta_ref = {}

        def read_loose(batch_):
            for sha1_, file in batch_:
                if type(file) is LoosePath:
                    try:
                        objtype_ = file.read_objtype(sha1_)
                    except Exception as e_:
                        logger.warning(f'dict_objtype: Failed to read objtype from sha1={sha1_}, {e_}')
                        continue
                    result[sha1_] = objtype_
                # TODO: cache object header of unread pack object and get objtype from it

        def offset_to_ref(sha1_, offset_delta_):
            try:
                pack = self.dict_object_from[sha1_]
                offset_base = pack.dict_offset[sha1_][0]
            except KeyError as e_:
                logger.warning(e_)
                return None
            offset = offset_base - offset_delta_
            if offset < 0:
                logger.warning(f'dict_objtype: OFS_DELTA object offset < 0, '
                               f'sha1={sha1_}, offset_base={offset_base}, offset_delta={offset_delta_}')
                return None
            try:
                return pack.dict_offset_to_sha1[offset]
            except KeyError:
                # print(sorted(pack.dict_offset_to_sha1))
                logger.warning(f'dict_objtype: OFS_DELTA offset not exist, offset={offset}'
                               f'sha1={sha1_}, offset_base={offset_base}, offset_delta={offset_delta_}')
                return None

        with THREAD_POOL.wait_jobs() as pool:
            for batch in batch_generator(self.dict_object_unread.items()):
                pool.start_thread_soon(read_loose, batch)

            # populate all objdata to git object
            dict_object = self.dict_object
            dict_object_data = self.dict_object_data
            for sha1, data in self.dict_object_data.items():
                try:
                    obj = parse_objdata(data)
                    dict_object[sha1] = obj
                except Exception as e:
                    logger.warning(f'dict_objtype: obj parse failed, sha1={sha1}, {e}')
            dict_object_data.clear()

            # while the thread are working, we read from cached data, which is CPU-bound
            dict_object = self.dict_object
            for sha1, obj in dict_object.items():
                try:
                    objtype = obj.type
                    if objtype in OBJTYPE_BASIC:
                        result[sha1] = objtype
                    elif objtype == 7:
                        ref = parse_ref_delta_ref(obj.data)
                        delta_ref[sha1] = ref
                    elif objtype == 6:
                        offset_delta = parse_ofs_delta_offset(obj.data)
                        ref = offset_to_ref(sha1, offset_delta)
                        delta_ref[sha1] = ref
                except Exception as e:
                    logger.warning(f'dict_objtype: obj parse failed, sha1={sha1}, {e}')

        # solve delta
        for sha1 in delta_ref:
            ref_from = sha1
            while True:
                ref_to = delta_ref.get(ref_from)
                if ref_to:
                    ref_from = ref_to
                    continue
                try:
                    obj_type = result[ref_from]
                    result[sha1] = obj_type
                except KeyError:
                    logger.warning(f'dict_objtype: failed to solve {sha1}, ref chain ends at {ref_from}')
                break

        return result

    def list_commit_have(self, sha1, have_lookback=20):
        """
        List commits before given sha1 (include given sha1) on the same branch

        Args:
            sha1 (str):
            have_lookback (int): Maximum lookback, 0 to return all

        Returns:
            dict[str, CommitObj]:
                Key: sha1 in str
                value: CommitObj
        """
        out = {}
        count = 0
        while True:
            obj = self.cat(sha1)
            commit = obj.decoded
            out[sha1] = commit
            count += 1

            parent = commit.parent
            parent_type = type(parent)
            if parent_type is str:
                sha1 = parent
            elif parent_type is list:
                # merge commit, pick the first parent
                sha1 = parent[0]
            elif not parent:
                # initial commit, no parent
                break

            # check if reached limit
            if have_lookback:
                if count >= have_lookback:
                    break

        return out
