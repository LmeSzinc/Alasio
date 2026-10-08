from collections import deque
from hashlib import sha1
from typing import Dict, Iterator

from alasio.deploy.pack.pack_model import FileInfo, RefInfo
from alasio.deploy_dev.pack import _pack_cache
from alasio.ext.algorithm.bit2coding.bit2coding_encode_c import encode_bit2
from alasio.ext.algorithm.bit2coding.vlenint_encode_c import encode_vlenint
from alasio.ext.algorithm.pathcomb.pathcomb_encode_c import encode_path_comb
from alasio.ext.algorithm.vint import encode_vint
from alasio.ext.cache import cached_property

# The pack area of the tree, relative to the tree root: the pack files of a
# version live directly under it, see validate_pack_area(). The constant keeps
# the trailing separator so the check is a plain startswith(), without building
# a prefix on every record; PACK_AREA_DIR is the folder itself, it must be a
# folder, never a file of a version.
PACK_AREA = '.pack/'
PACK_AREA_DIR = PACK_AREA[:-1]


def validate_pack_area(path):
    """
    Validate the pack area depth of a record path.

    The pack area .pack of a tree holds the pack files of the version directly
    under it: index.pack (the ledger slot of the packs), history.pack, and any
    other pack area file. The folder itself must be a folder, never a file of a
    version, and a path nested deeper, e.g. .pack/httpx/index.pack, is ambiguous
    with the path organization of a deploy target: the client maps every .pack
    path into the ledger folder of its target ({root}/.pack, or
    {root}/.pack/{name} for a named target), so a nested record would alias the
    ledger folder, its workspace or another pack file of the target.

    Args:
        path (str): File path of a pack record

    Raises:
        ValueError: If the path is the pack area itself, or lies under it
            deeper than one level
    """
    if path == PACK_AREA_DIR:
        raise ValueError(
            f'Pack path is the pack area itself, it must be a folder: {path!r}'
        )
    if not path.startswith(PACK_AREA):
        return
    # a separator behind the prefix means a path nested deeper than one level
    if path.find('/', len(PACK_AREA)) != -1:
        raise ValueError(
            f'Pack path is nested too deep in the pack area: {path!r}, '
            f'only files directly under {PACK_AREA} are allowed'
        )


def encode_pack_version(pack_version):
    """
    Encode a pack format version into the single byte of the pack header.

    The version is an int in Python (see PackEncodeBase.PACK_VERSION) and one
    byte in a pack file, the byte behind b'PACK', so 0~255 is the whole range
    the format carries and a version bump stays a one byte change on disk.

    Args:
        pack_version (int): Pack format version, 0~255

    Returns:
        bytes: The single byte of the version

    Raises:
        ValueError: If pack_version is not an int in 0~255
    """
    if not isinstance(pack_version, int) or not 0 <= pack_version <= 0xFF:
        raise ValueError(f'Pack version must be an int in 0~255, got {pack_version!r}')
    return bytes((pack_version,))


class PackEncodeBase:
    """
    Alasio 更新模块

    存在 3 种文件：
    - 全量包 (full pack)，可解压出某个版本中的全部文件
    - 增量包 (update pack)，可读取现存A版本的本地文件 增量更新到B版本
    - 索引包 (index pack)，记录版本中所有文件的信息，作为普通文件储存在 .pack/index.pack

    三种文件共享如下数据结构：
    - 全量包 (full pack)
      data section 中记录的是完整文件
      index section 中记录的是 data section 的信息，也就是当前版本所有文件的信息
      refinfo 为空，old version 为空
    - 增量包 (update pack)
      data section 是 zstd 增量更新数据，增量更新数据必须输入旧文件才能解压，无法独立解压
      index section 中记录的是 data section 的信息，也就是所有增量数据的信息
      refinfo 记录旧版本文件，.pack/index.pack 作为普通文件记录在 refinfo / fileinfo 中
      old version 记录旧版本号
    - 全量包的前面部分就是索引包，去除 data section 之后的部分

    全量包和增量包由 version part 的 old version 区分：
    old version 为空则是全量包，非空则是增量包，不再由 refinfo 是否为空来判断
    （refinfo 只记录解压需要参照的旧文件，是增量包的实现细节）

    # header
    - b'PACK'
    - PACK version, one byte, an int in 0~255 on the Python side

    # index section
    - length (including checksum of index section)
        # version part
        - length
            - current version in string
        - length
            - old version in string, empty in full pack
            (a non-empty old version makes the pack an update pack)
        # data length part
        - length
            - data_section_length_vint
            (the length vint of the data section, so the data section
            offset can be derived from an index pack directly)
        # index part
        - length
            - index_data
        # sha1 part
        - length
            - sha1
        # checksum
        - checksum (checksum of above, including header and length)

    # data section
    - length (including checksum of file data)
        - file_data
        - checksum (checksum of above, including all)

    全量解压流程与增量更新流程：
    - 申请 .pack/index.pack 的排它锁，防止竞争操作
    - 复制全量包到 .pack/workspace/job.pack
      这样即使解压中断 在下一次运行也能恢复
      申请到锁的进程需要先检查 job.pack 是否有未完成的任务，需要先完成未完成的任务
    - 在全量包中解压索引块写入 .pack/index.pack，就是全量包的前面部分
      在增量包中 .pack/index.pack 是普通文件记录，像其他文件一样更新
    - 索引块可解码出 current version / old version / refinfo / fileinfo
      fileinfo 是当前包拥有的数据的索引
      refinfo 是解压数据需要参照的旧文件的索引
      old version非空就是增量包，为空则是全量包
    - 根据索引块尝试读取目标文件，如果目标文件存在且size+sha1校验通过则跳过
    - 将文件解压到临时文件 .pack/workspace/{size}_{sha1}_{index}.tmp
      如果临时文件存在且size+sha1校验通过则跳过
      - edit=A (added) 直接解压
      - 增量包中有多种edit模式
        以 edit=M (modified) 为例：根据 refinfo 读取已有文件，检查 size sha1，使用zstd解压
    - 将临时文件移动到目标路径
      这样保证了文件内容的原子性，文件列表的原子性由任务恢复保证
    - 清空 .pack/workspace 文件夹，包括job.pack和剩余未知的{size}_{sha1}_{index}.tmp
    - 释放 .pack/index.pack 的锁

    文件校验流程：
    - 从http获取 latest.pack，包含最新版本sha1 和 对应索引包的sha1 checksum
      与本地索引包的版本进行比对
      - 如果不一致则下载增量包 /{new_version}/from_{old_version}.pack ，进入增量更新流程
      - 如果一致则继续文件校验流程
    - 校验本地索引包 .pack/index.pack 的sha1 checksum，与latest.pack的sha1 checksum比对
      - 如果不一致则使用 http range 请求从 /{new_version}/full_{new_version}.pack 下载索引块
        - 请求大约 range=0~9 将包含 header + 索引块长度
        - 请求 range = 0 ~ len(header)+len(index_section)
        - 替换 .pack/index.pack
    - 根据索引包校验所有记录文件的 size+sha1
      - 如果文件不一致则收集所有不一致的文件信息，进入校验流程：
        - 申请 .pack/index.pack 的排它锁，防止竞争操作
        - 写入特定内容到 .pack/workspace/job.pack 标记正在执行校验任务
        - 根据索引记录的 data_size 计算出目标文件在全量包的位置
        - 使用 http range 请求下载文件，同样解压到临时文件 .pack/workspace/{size}_{sha1}_{index}.tmp
          如果临时文件存在且size+sha1校验通过则跳过
          如果特定区块无法下载或者下载的内容校验不通过则跳过，这是无法解决的问题
        - 将临时文件移动到目标路径
        - 清空 .pack/workspace 文件夹
        - 释放 .pack/index.pack 的锁
    """
    # pack format version of the encoded bytes: an int in 0~255, the pack file
    # carries it as the single byte behind b'PACK'. A published pack must be
    # rebuilt with the format version it was encoded with, see PackUpdate
    PACK_VERSION = 0

    def __init__(self):
        # cache of the pipeline that encodes this pack: the process cache, read
        # through the module at construction, so a caller that swapped it before
        # the build (a test, a benchmark) validates and encodes with the fresh
        # one, see _pack_cache. The wheel encoders bind a cache of their own
        # instead, see PackWheel
        self.cache = _pack_cache.PACK_CACHE
        # version of this pack, e.g. the commit sha1 of the packed version
        self.current_version: str = ''
        # version this pack updates from, empty in a full pack, a
        # non-empty value makes the pack an update pack
        self.old_version: str = ''
        # pack format version of this pack, see PACK_VERSION
        self.pack_version = self.PACK_VERSION
        # checksum of the full pack bytes, the trailing 20 bytes digest of the
        # data section, the same digest the decoder's validate_data() verifies.
        # Cached by iter_pack_data() while the pack is emitted, None until then
        self.full_pack_checksum: "bytes | None" = None
        # checksum of the index section, the trailing 20 bytes of the index
        # pack, the digest the client compares its local .pack/index.pack
        # against, see latest_pack(). Cached by iter_packidx_data() while the
        # index is emitted, None until then
        self.index_pack_checksum: "bytes | None" = None

    @property
    def pack_version(self):
        """
        Pack format version of this pack, an int in 0~255

        Returns:
            int: Pack format version, see PACK_VERSION
        """
        return self._pack_version

    @pack_version.setter
    def pack_version(self, pack_version):
        # the encode of the header byte, run here as the check: an out of range
        # version fails at construction, not at the assembly of the pack
        encode_pack_version(pack_version)
        self._pack_version = pack_version

    @cached_property
    def refinfo(self) -> "Dict[str, RefInfo]":
        return {}

    @cached_property
    def fileinfo(self) -> "Dict[str, FileInfo]":
        return {}

    def _iterfile(self, iter_ref=False, iter_file=False) -> "Iterator[FileInfo]":
        if iter_ref and self.refinfo:
            yield from self.refinfo.values()
        if iter_file and self.fileinfo:
            yield from self.fileinfo.values()

    def _iterfile_with_content(self, iter_ref=False, iter_file=False) -> "Iterator[FileInfo]":
        if iter_ref and self.refinfo:
            # encode all RefInfo
            yield from self.refinfo.values()
        if iter_file and self.fileinfo:
            for file in self.fileinfo.values():
                # deleted file has no info
                if file.edit == 2:
                    continue
                # C (copied) files should reuse the info of source file
                if file.edit == 0 and file.source_lookback:
                    continue
                yield file

    def iter_index_data(self):
        # length of: RefInfo
        yield encode_vint(len(self.refinfo))
        # length of: FileInfo
        yield encode_vint(len(self.fileinfo))

        # every record path, a pack must not carry unsafe or ambiguous
        # paths: reject absolute / traversal paths, names that cannot be
        # unpacked on some platform, and pack area paths nested deeper
        # than one level. The paths the encoders already validated (a .py
        # file of a version walked before) are looked up in the cache of the
        # encoder instead of being validated again, see
        # PackCache.validate_record_path
        cache = self.cache
        files = list(self._iterfile(iter_ref=True, iter_file=True))
        for file in files:
            cache.validate_record_path(file.path)

        # filepath, the resulting sections are written one after another
        list_prefix_comb, list_suffix_comb, path_data = encode_path_comb(
            file.path for file in self._iterfile(iter_ref=True, iter_file=True))
        yield encode_vlenint(list_prefix_comb)
        yield encode_vlenint(list_suffix_comb)
        yield path_data

        # edit edit
        list_edit = [file.edit for file in self._iterfile(iter_file=True)]
        yield encode_bit2(list_edit)

        # source lookback
        # deleted file has no source lookback
        list_source_lookback = [
            file.source_lookback for file in self._iterfile(iter_file=True)
            if file.edit != 2
        ]
        yield encode_vlenint(list_source_lookback)

        # file info
        list_eol = deque()
        list_mode = deque()
        list_algo = deque()
        list_size = deque()
        list_data_size = deque()
        for file in self._iterfile_with_content(iter_ref=True):
            list_size.append(file.size)
        # eol / mode of all non-D fileinfo, C (copied) records carry their own
        for file in self._iterfile(iter_file=True):
            if file.edit == 2:
                continue
            list_eol.append(file.eol)
            list_mode.append(file.mode)
        for file in self._iterfile_with_content(iter_file=True):
            list_algo.append(file.algo)
            list_size.append(file.size)
            # skip data_size for raw files
            # encode size diff as data_size
            # R (renamed) records have no data, their diff is the full size
            if file.algo != 0 or file.edit == 3:
                if file.data_size > file.size:
                    raise ValueError(f'File data_size must be <= size: {file}')
                list_data_size.append(file.size - file.data_size)

        yield encode_bit2(list_eol)
        yield encode_bit2(list_mode)
        yield encode_bit2(list_algo)
        yield encode_vlenint(list_size)
        yield encode_vlenint(list_data_size)

    def iter_sha1_data(self) -> "Iterator[bytes]":
        for file in self._iterfile_with_content(iter_ref=True):
            # this shouldn't happen
            if not file.sha1:
                raise ValueError(f'Empty sha1 from {file}')
            yield file.sha1
        for file in self._iterfile_with_content(iter_file=True):
            # sha1 of empty content is always the same, no need to store it
            if file.data_size == 0:
                continue
            # this shouldn't happen
            if not file.sha1:
                raise ValueError(f'Empty sha1 from {file}')
            yield file.sha1

    def iter_file_data(self) -> "Iterator[bytes]":
        for file in self._iterfile_with_content(iter_file=True):
            length = len(file.data)
            if length != file.data_size:
                raise ValueError(f'File data_size inconsistant: {file}')
            if length:
                yield file.data

    def iter_packidx_data(self):
        def iter_header():
            yield b'PACK'
            yield encode_pack_version(self.pack_version)

        def iter_index():
            # version
            # the current version, then the old version to update from
            # (empty in a full pack), each one with its own length as header
            for value in (self.current_version, self.old_version):
                encoded = value.encode('utf-8')
                yield encode_vint(len(encoded))
                yield encoded

            # data length
            # the length vint of the data section, so the data section
            # offset can be derived from an index pack directly
            data_length = sum(
                file.data_size for file in self._iterfile_with_content(iter_file=True)
            ) + 20
            data_length_vint = encode_vint(data_length)
            yield encode_vint(len(data_length_vint))
            yield data_length_vint

            # index data
            index_data = b''.join(self.iter_index_data())
            yield encode_vint(len(index_data))
            yield index_data

            # sha1
            sha1_data = b''.join(self.iter_sha1_data())
            yield encode_vint(len(sha1_data))
            yield sha1_data

        # header
        checksum = sha1()
        for row in iter_header():
            yield row
            checksum.update(row)

        # length of index section
        data = list(iter_index())
        length = sum([len(row) for row in data]) + 20
        length_vint = encode_vint(length)
        yield length_vint
        checksum.update(length_vint)
        # index section
        for row in data:
            yield row
            checksum.update(row)
        # checksum (checksum of above, including header and length)
        # cached before it is yielded: latest_pack() builds the payload of the
        # version from it
        self.index_pack_checksum = checksum.digest()
        yield self.index_pack_checksum

    def iter_pack_data(self):
        """
        # index section
        ...

        # files
        - length (including checksum of file data)
            - file_data
            - checksum (checksum of above, including all)

        The trailing checksum is cached on full_pack_checksum while it is
        emitted, so the assembled full pack can be described without
        assembling it again.
        """
        # header and index section
        # the index rows are hashed once more here on purpose: this checksum
        # covers the whole index section, the index checksum computed in
        # iter_packidx_data() is another digest, see PackDecodeBase.validate
        checksum = sha1()
        for row in self.iter_packidx_data():
            yield row
            checksum.update(row)

        # length of data section
        data = list(self.iter_file_data())
        length = sum([len(row) for row in data]) + 20
        length_vint = encode_vint(length)
        yield length_vint
        checksum.update(length_vint)
        # data section
        for row in data:
            yield row
            checksum.update(row)
        # checksum (checksum of above, including all)
        # cached before it is yielded: the consumer that reads the last row of
        # the pack finds the checksum of the assembled pack on the instance
        self.full_pack_checksum = checksum.digest()
        yield self.full_pack_checksum

    def latest_pack(self, index_checksum=None):
        """
        Payload of the latest.pack file: current version + index pack checksum

        The version is written in utf-8, followed by the 20 bytes checksum of
        the index pack: the trailing 20 bytes of the index section of the pack
        (see PackDecodeBase.index_checksum), the digest the client compares
        its local .pack/index.pack against (ResetJob.validate_latest) and
        validates a downloaded index pack with (ResetJob.download_index). It
        is the layout ServerFile.get_latest_info() reads back. The checksum is
        cached by iter_packidx_data() (and by iter_pack_data(), which emits
        the index section first), consume one of them (e.g. write the pack to
        disk) before calling this method.

        Args:
            index_checksum (bytes, optional): Checksum to write instead of the
                cached one, the trailing 20 bytes digest read back from a pack
                file, see PackDecodeBase.read_index_checksum: a caller that
                keeps the full pack of an earlier run never emitted the index
                pack of this run. Defaults to None, the cached checksum.

        Returns:
            bytes: Current version in utf-8 bytes + 20 bytes index pack checksum

        Raises:
            ValueError: If the index pack has not been emitted yet and no
                index_checksum is given, or the checksum is not 20 bytes long
        """
        if index_checksum is None:
            index_checksum = self.index_pack_checksum
            if index_checksum is None:
                raise ValueError(
                    'Failed to build latest.pack: index pack checksum unknown, '
                    'consume iter_packidx_data() first'
                )
        if len(index_checksum) != 20:
            raise ValueError(
                f'Failed to build latest.pack: index pack checksum of {len(index_checksum)} bytes, '
                f'expected the 20 bytes digest, see PackDecodeBase.read_index_checksum'
            )
        return self.current_version.encode('utf-8') + index_checksum
