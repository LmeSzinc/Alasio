from collections import deque
from hashlib import sha1
from typing import Dict, Iterator

from alasio.backport import removeprefix
from alasio.deploy.pack.pack_model import FileInfo, RefInfo
from alasio.ext.algorithm.bit2coding import encode_bit2
from alasio.ext.algorithm.lcp import get_lcp
from alasio.ext.algorithm.pathlcs import PathLookbackLCS
from alasio.ext.algorithm.pathlen_coding import (
    MAX_PREFIX_REUSE, MAX_SUFFIX_LOOKBACK, MAX_SUFFIX_REUSE, encode_prefix_comb, encode_suffix_comb
)
from alasio.ext.algorithm.vint import encode_vint
from alasio.ext.algorithm.vlenint import encode_vlenint
from alasio.ext.cache import cached_property


class PackEncodeBase:
    """
    Alasio 更新模块

    存在 3 种文件：
    - 全量包 (full pack)，可解压出某个版本中的全部文件
    - 增量包 (update pack)，可读取现存A版本的本地文件 增量更新到B版本
    - 索引包 (index pack)，记录版本中所有文件的信息，作为普通文件储存在 .pack/index.pack

    三种文件共享如下数据结构：
    - 全量包中没有 index update part
      data section 中记录的是完整文件
      index section 中记录的是 data section 的信息，也就是当前版本所有文件的信息
    - 增量包中有 index update part
      data section 是 zstd 增量更新数据，增量更新数据必须输入旧文件才能解压，无法独立解压
      index section 中记录的是 data section 的信息，也就是所有增量数据的信息
    - 全量包的前面部分就是索引包，去除 data section 之后的部分

    # header
    - b'PACK'
    - PACK version

    # index section
    - length (including checksum of index section)
        # version part
        - length
            - latest commit sha1 in string
        # index part
        - length
            - index_data
        # sha1 part
        - length
            - sha1
        # index update part
        - length
            - index_update
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
      在增量包中使用 index update part 更新 .pack/index.pack
      存在index update part就是增量包，不存在则是全量包
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
    - 从http获取 latest.dat，包含最新版本sha1 和 对应索引包的sha1 checksum
      与本地索引包的版本进行比对
      - 如果不一致则下载增量包 /{new_version}/from_{old_version}.pack ，进入增量更新流程
      - 如果一致则继续文件校验流程
    - 校验本地索引包 .pack/index.pack 的sha1 checksum，与latest.dat的sha1 checksum比对
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
    def __init__(self):
        self.latest_commit: str = ''
        self.pack_version = b'\x00'

    @cached_property
    def refinfo(self) -> "Dict[str, RefInfo]":
        return {}

    @cached_property
    def fileinfo(self) -> "Dict[str, FileInfo]":
        return {}

    @cached_property
    def index_update(self) -> bytes:
        """
        zstd compressed data to update index_data
        used in update pack only, empty in idx pack and full pack
        """
        return b''

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

        # filepath
        prev = ''
        list_path: "deque[bytes]" = deque()
        list_prefix_reuse = deque()
        list_path_length = deque()
        list_suffix_lookback = deque()
        list_suffix_reuse = deque()
        lcs_lookback = PathLookbackLCS()
        for file in self._iterfile(iter_ref=True, iter_file=True):
            # prefix
            path = file.path
            prefix_reuse = get_lcp(prev, path)
            # prefix_reuse must <= MAX_PREFIX_REUSE
            # otherwise the zigzag diff may overflow the combined-int encoding
            if len(prefix_reuse) > MAX_PREFIX_REUSE:
                prefix_reuse = prefix_reuse[:MAX_PREFIX_REUSE]
            path = removeprefix(path, prefix_reuse)
            list_prefix_reuse.append(len(prefix_reuse))
            prev = file.path

            # suffix
            # query with the full path, consistent with add_path() below and
            # with the decoder, which takes suffixes from full lookback paths;
            # a prefix-stripped path may lose its extension dot (e.g. "png")
            # and can never match the ('.png', ...) buckets of stored paths
            suffix_lookback, suffix_reuse = lcs_lookback.get_lcs(
                file.path, min_length=3, max_length=MAX_SUFFIX_REUSE, max_lookback=MAX_SUFFIX_LOOKBACK,
            )
            # the LCS of full paths may extend beyond the prefix-stripped path
            # (e.g. ".png" vs stripped "png"); cap it so the suffix always fits
            # the remaining path, keeping prefix and suffix non-overlapping
            if suffix_reuse > len(path):
                suffix_reuse = len(path)
                # a zero-length reuse must not keep a lookback: the decoder
                # takes ``paths[i-lookback][-suffix_reuse:]`` and ``[-0:]``
                # would yield the whole referenced path instead of nothing
                if not suffix_reuse:
                    suffix_lookback = 0
            if suffix_reuse:
                path = path[:-suffix_reuse]
            lcs_lookback.add_path(file.path)
            list_suffix_lookback.append(suffix_lookback)
            list_suffix_reuse.append(suffix_reuse)

            # remaining path
            if path:
                path = path.encode()
                list_path.append(path)
                list_path_length.append(len(path))
            else:
                list_path_length.append(0)

        list_prefix_comb = encode_prefix_comb(list_prefix_reuse, list_path_length)
        yield encode_vlenint(list_prefix_comb)
        list_suffix_comb = encode_suffix_comb(list_suffix_reuse, list_suffix_lookback)
        yield encode_vlenint(list_suffix_comb)
        yield b''.join(list_path)

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
        for file in self._iterfile_with_content(iter_file=True):
            list_eol.append(file.eol)
            list_mode.append(file.mode)
            list_algo.append(file.algo)
            list_size.append(file.size)
            # skip data_size for raw files
            # encode size diff as data_size
            if file.algo != 0:
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
            yield bytes.fromhex(file.sha1)
        for file in self._iterfile_with_content(iter_file=True):
            # sha1 of empty content is always the same, no need to store it
            if file.data_size == 0:
                continue
            # this shouldn't happen
            if not file.sha1:
                raise ValueError(f'Empty sha1 from {file}')
            yield bytes.fromhex(file.sha1)

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
            yield self.pack_version

        def iter_index():
            # version
            latest_commit = self.latest_commit.encode('utf-8')
            yield encode_vint(len(latest_commit))
            yield latest_commit

            # index data
            index_data = b''.join(self.iter_index_data())
            yield encode_vint(len(index_data))
            yield index_data

            # sha1
            sha1_data = b''.join(self.iter_sha1_data())
            yield encode_vint(len(sha1_data))
            yield sha1_data

            # index update
            index_update = self.index_update
            yield encode_vint(len(index_update))
            yield index_update

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
        yield checksum.digest()

    def iter_pack_data(self):
        """
        # index section
        ...

        # files
        - length (including checksum of file data)
            - file_data
            - checksum (checksum of above, including all)
        """
        # header and index section
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
        yield checksum.digest()
