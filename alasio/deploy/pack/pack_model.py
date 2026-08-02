from msgspec import UNSET, Struct, UnsetType


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


class IdxInfo(FileInfo):
    data: UnsetType = UNSET
