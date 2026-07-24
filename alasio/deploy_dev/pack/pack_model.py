from hashlib import sha1

from msgspec import Struct, UNSET, UnsetType

from alasio.ext.compress.algo_lzma import lzma_compress
from alasio.ext.compress.algo_zstd import zstd_compress
from alasio.logger import logger


class RefInfo(Struct):
    """
    A record of old file
    """
    # filepath like "folder/file.py"
    path: str
    # file size
    size: int = 0
    # git file sha1, length=40 (not sha1 of file content)
    # or '' if file should not exist
    sha1: str = ''


class FileInfo(RefInfo):
    # edit mode
    # 0 for A (added)
    #   - C (copied), if source_path is not empty, edit is C
    #     meaning a new file (A) whose blob hash is identical to any file in the parent commit tree or previous files
    # 1 for M (modified)
    # 2 for D (deleted)
    # 3 for R (renamed), file is moved to new path
    #   - RM (renamed+modified), if data is not empty
    edit: int = 0
    # line ending
    # `eol` is meaningful only if `edit` is not D (deleted)
    # 0 for LF
    # 1 for CRLF
    # 2 for binary
    eol: int = 0
    # file mode
    # `mode` is meaningful only if `edit` is not D (deleted)
    # 0 for filemode 644
    # 1 for filemode 755
    mode: int = 0
    # compress algorithm
    # `algo` is meaningful only if `edit` is M/A/RM
    # 0 for raw, no compress
    # 1 for lzma compression
    # 2 for zstd compression
    #   - if algo=zstd and source_lookback==0, uncompress from zstd data
    #   - if algo=zstd and source_lookback!=0, read source path and apply patch, similar to `zstd -d --patch-from`
    algo: int = 0
    # file data
    # data is meaningful only if `edit` is M/A/RM
    # if algo is raw, data is file content
    # if algo is lzma/zstd, data is compressed data
    # if algo is others, data is b''
    data: bytes = b''
    # compressed data size in full pack
    data_size: int = 0
    # source_lookback that indicates previous file
    # if edit is R/RM, source_lookback is the file to be renamed from
    # if edit is C, source_lookback is the file to be copied from
    # if edit is others, source_lookback is "0
    source_lookback: int = 0

    @classmethod
    def new_deleted(cls, path):
        """
        Create an empty record to indicate a file that should not exist

        Args:
            path (str):
        """
        return cls(path=path, edit=2)

    def load_git_mode(self, mode):
        """
        Convert git entry mode to our RefInfo.mode, and set to self

        Args:
            mode (bytes):

        Returns:
            int:
        """
        if mode == b'100644':
            self.eol = 0
        elif mode == b'100755':
            self.eol = 1
        elif mode == b'120000':
            logger.warning(f'RefInfo does not support symlink yet, file="{self.path}"')
            self.eol = 0
        else:
            # 040000 and 160000 should be handled by list_files() so nothing should hit here
            logger.warning(f'RefInfo gets unknown git entry mode {mode}, file="{self.path}')
            self.eol = 0

    # @classmethod
    # def from_refinfo(cls, ref: "RefInfo"):
    #     """
    #     Create an empty FileInfo from RefInfo
    #     """
    #     return cls(path=ref.path, size=ref.size, sha1=ref.sha1, mode=ref.mode, eol=ref.eol)

    def __repr__(self):
        """
        Returns:
            str: Representation of FileInfo excluding the data field
        """
        fields = []
        for name in self.__struct_fields__:
            value = getattr(self, name)
            # hide data, since data is large
            if name == 'data':
                continue
            fields.append(f'{name}={value!r}')

        return f'{type(self).__name__}({", ".join(fields)})'

    __str__ = __repr__

    def load_data(self, data, source=None, zstd=True):
        """
        Find the best compress algorithm to store data, and set `algo` and `data`

        Args:
            data (bytes):
            source (bytes): Optional old file content for zstd
            zstd (bool):
        """
        best_length = len(data)
        # empty file, treat as raw
        if best_length == 0:
            self.algo = 0
            self.data = data
            self.data_size = 0
            self.size = 0
            self.sha1 = ''
            return
        best_data = data
        algo = 0

        # try lzma compression
        compressed_data = lzma_compress(data)
        compressed_length = len(compressed_data)
        if compressed_length < best_length:
            best_length = compressed_length
            best_data = compressed_data
            algo = 1
        else:
            del compressed_length
            del compressed_data

        if zstd:
            # try zstd --patch-from
            if source is not None:
                compressed_data = zstd_compress(data, source=source)
                compressed_length = len(compressed_data)
                if compressed_length < best_length:
                    best_length = compressed_length
                    best_data = compressed_data
                    algo = 2
                else:
                    del compressed_length
                    del compressed_data

            # try plain zstd compression
            compressed_data = zstd_compress(data, source=source)
            print(compressed_data[:20])
            compressed_length = len(compressed_data)
            if compressed_length < best_length:
                best_length = compressed_length
                best_data = compressed_data
                algo = 2
            else:
                del compressed_length
                del compressed_data

        # set
        self.algo = algo
        self.data = best_data
        self.data_size = best_length
        self.size = len(data)
        self.sha1 = sha1(data).hexdigest()


class IdxInfo(FileInfo):
    data: UnsetType = UNSET
