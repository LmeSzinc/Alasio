from collections import deque
from hashlib import sha1
from typing import Dict, Iterator

from alasio.backport import removeprefix
from alasio.deploy_dev.pack.pack_model import FileInfo, RefInfo
from alasio.ext.algorithm.bit2coding import encode_bit2
from alasio.ext.algorithm.lcp import get_lcp
from alasio.ext.algorithm.pathlcs import PathLookbackLCS
from alasio.ext.algorithm.pathlen_coding import encode_prefix_comb, encode_suffix_comb
from alasio.ext.algorithm.vint import encode_vint
from alasio.ext.algorithm.vlenint import encode_vlenint
from alasio.ext.cache import cached_property
from alasio.ext.compress.algo_lzma import lzma_compress


class PackEncodeBase:
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
        """
        return b''

    @cached_property
    def sha1_update(self) -> bytes:
        """
        zstd compressed data to update sha1_data
        """
        return b''

    @cached_property
    def history_data(self) -> bytes:
        """
        Commit info in messagepack
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
            # prefix_reuse must <= 65535
            if len(prefix_reuse) > 65535:
                prefix_reuse = prefix_reuse[:65535]
            path = removeprefix(path, prefix_reuse)
            list_prefix_reuse.append(len(prefix_reuse))
            prev = file.path

            # suffix
            suffix_lookback, suffix_reuse = lcs_lookback.get_lcs(
                path, min_length=3, max_length=65535, max_lookback=255)
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
                diff = max(file.size - file.data_size, 0)
                list_data_size.append(diff)

        yield encode_bit2(list_eol)
        yield encode_bit2(list_mode)
        yield encode_bit2(list_algo)
        yield encode_vlenint(list_size)
        yield encode_vlenint(list_data_size)

    def iter_sha1_data(self) -> "Iterator[bytes]":
        for file in self._iterfile_with_content(iter_ref=True, iter_file=True):
            if file.data_size == 0:
                continue
            # this shouldn't happen
            if not file.sha1:
                raise ValueError(f'Empty sha1 from {file}')
            yield bytes.fromhex(file.sha1)

    def iter_file_data(self) -> "Iterator[bytes]":
        for file in self._iterfile_with_content(iter_ref=True, iter_file=True):
            length = len(file.data)
            if length != file.data_size:
                raise ValueError(f'File data_size inconsistant: {file}')
            if length:
                yield file.data

    def iter_packidx_data(self):
        """
        # header
        - b'PACK'
        - PACK version

        # index section
        - length (including checksum of index section)
            # index part
            - length
                - lzma(index_data)
            # sha1 part
            - length
                - sha1
            # index update part
            - length
                - index_update
            # sha1 update part
            - length
                - sha1_update
            # version part
            - length
                - latest commit sha1 in string
            # commit history part
            - length
                - lzma(history_data)
            # checksum
            - checksum (checksum of above, including header and length)

        # files
        - length (including checksum of file data)
            - file_data
            - checksum (checksum of above, including all)
        """

        def iter_header():
            yield b'PACK'
            yield self.pack_version

        def iter_index():
            # index data
            index_data = b''.join(self.iter_index_data())
            if index_data:
                index_data = lzma_compress(index_data)
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

            # sha1 update
            sha1_update = self.sha1_update
            yield encode_vint(len(sha1_update))
            yield sha1_update

            # version
            latest_commit = self.latest_commit.encode('utf-8')
            yield encode_vint(len(latest_commit))
            yield latest_commit

            # commit history
            history_data = self.history_data
            if history_data:
                history_data = lzma_compress(history_data)
            yield encode_vint(len(history_data))
            yield history_data

        checksum = sha1()
        for row in iter_header():
            yield row
            checksum.update(row)

        data = list(iter_index())
        length = sum([len(row) for row in data]) + 20
        yield encode_vint(length)
        for row in data:
            yield row
            checksum.update(row)

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
        checksum = sha1()
        for row in self.iter_packidx_data():
            yield row
            checksum.update(row)

        data = list(self.iter_file_data())
        length = sum([len(row) for row in data]) + 20
        yield encode_vint(length)
        for row in data:
            yield row
            checksum.update(row)
        yield checksum.digest()
