
from hashlib import sha1

from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext.algorithm.bit2coding import decode_bit2
from alasio.ext.algorithm.pathlen_coding import decode_prefix_comb, decode_suffix_comb
from alasio.ext.algorithm.vint import decode_vint
from alasio.ext.algorithm.vlenint import decode_vlenint
from alasio.ext.cache import cached_property
from alasio.ext.compress.algo_lzma import lzma_decompress
from alasio.ext.compress.algo_zstd import zstd_decompress


class PackDecodeError(ValueError):
    """
    Raised when a pack file fails to decode or validate.

    The message includes the section being decoded when the failure
    happened, e.g. "[index data: edits] ...".
    """


def _decode(section, func, *args):
    """
    Run a decode call, wrapping ValueError into PackDecodeError.

    Args:
        section (str): Section name for the error message
        func (Callable): Decode function, e.g. decode_vint
        *args: Arguments to pass to func

    Returns:
        object: func result
    """
    try:
        return func(*args)
    except ValueError as e:
        raise PackDecodeError(f'Failed to decode {section}: {e}') from e


def _check_length(section, actual, expected):
    """
    Verify a decoded value count matches the expected count.

    Args:
        section (str): Section name for the error message
        actual (int): Decoded value count
        expected (int): Expected value count

    Raises:
        PackDecodeError: If counts differ
    """
    if actual != expected:
        raise PackDecodeError(
            f'Failed to decode {section}: decoded {actual} values, expected {expected}'
        )


class PackDecodeBase:
    """
    Decode and validate a pack file encoded by PackEncodeBase.

    Attributes:
        data (memoryview): Raw pack bytes.
        pack_version (bytes): PACK format version byte.
        version (str): Latest commit sha1 recorded in the pack.
        index_section (memoryview): Index section, from the length vint to the
            end of its checksum digest (excluding the header).
        data_section (memoryview): Data section, from the length vint to the
            end of its checksum digest. Empty in index pack.
        refinfo (dict[str, IdxInfo]): Old file records (empty in full pack).
        fileinfo (dict[str, IdxInfo]): New file records, keyed by path.
        idx_info (list[IdxInfo]): All records, refinfo entries first then
            fileinfo entries, in the encoded order.
    """

    def __init__(self, data):
        """
        Parse the pack structure. Use validate() to check checksums.

        Args:
            data (bytes | bytearray | memoryview): Raw pack file content

        Raises:
            PackDecodeError: If the pack structure is malformed
        """
        if isinstance(data, (bytes, bytearray)):
            data = memoryview(data)
        self.data = data

        # header
        if len(data) < 5 or bytes(data[:4]) != b'PACK':
            raise PackDecodeError(f'Failed to decode header: not a pack file: {bytes(data[:4])!r}')
        self.pack_version = bytes(data[4:5])

        # index section
        offset = 5
        length, read = _decode('index section: length', decode_vint, data[offset:])
        offset += read
        index_end = offset + length
        if index_end > len(data):
            raise PackDecodeError(
                f'Failed to decode index section: out of range: {index_end} > {len(data)}'
            )
        self.index_section = data[5:index_end]

        # index parts
        part, offset = _decode('index section: version part', self._read_part, data, offset)
        self.version = bytes(part).decode('utf-8', errors='replace')
        self._data_length, offset = _decode(
            'index section: data length part', self._read_part, data, offset)
        self._index_part, offset = _decode(
            'index section: index part', self._read_part, data, offset)
        self._sha1_part, offset = _decode(
            'index section: sha1 part', self._read_part, data, offset)
        self._index_update, offset = _decode(
            'index section: index update part', self._read_part, data, offset)
        if index_end - offset != 20:
            raise PackDecodeError(
                f'Failed to decode index section: checksum out of range: '
                f'{index_end} - {offset} != 20'
            )

        # data section (optional, index pack has no data section)
        data_offset = index_end
        if data_offset >= len(data):
            # index pack: header + index section only, file data unavailable
            self._has_data = False
            self.data_section = data[index_end:index_end]
            # the data section of the full pack is behind the data
            # section length vint, which is stored in the data length
            # part of the index section
            self._data_start = data_offset + len(self._data_length)
        else:
            self._has_data = True
            length, read = _decode('data section: length', decode_vint, data[data_offset:])
            data_offset += read
            data_end = data_offset + length
            if data_end > len(data):
                raise PackDecodeError(
                    f'Failed to decode data section: out of range: {data_end} > {len(data)}'
                )
            self.data_section = data[index_end:data_end]
            # file offset where the actual file data begins (after the length vint)
            self._data_start = data_offset

    @staticmethod
    def _read_part(data, offset):
        """
        Read a vint-length-prefixed part.

        Args:
            data (memoryview): Raw pack bytes
            offset (int): Current offset

        Returns:
            tuple[memoryview, int]: (part bytes, new offset)
        """
        length, read = decode_vint(data[offset:])
        offset += read
        end = offset + length
        if end > len(data):
            raise ValueError(f'Part out of range: offset={offset} length={length}')
        return data[offset:end], end

    def validate(self):
        """
        Validate the checksums of index section and data section.

        Raises:
            PackDecodeError: If any checksum mismatches, or the pack has
                no data section (index pack)
        """
        self.validate_index()
        self.validate_data()

    def validate_index(self):
        """
        Validate the checksum of index section.

        Raises:
            PackDecodeError: If the checksum mismatches
        """
        # index section checksum covers: header + length vint + parts
        digest = sha1()
        digest.update(self.data[:5])
        digest.update(self.index_section[:-20])
        if digest.digest() != bytes(self.index_section[-20:]):
            raise PackDecodeError('Failed to validate index checksum: checksum mismatch')

    def validate_data(self):
        """
        Validate the checksum of data section.

        Index packs have no data section, callers must not call this on
        them.

        Raises:
            PackDecodeError: If the checksum mismatches, or the pack has
                no data section (index pack)
        """
        if not self._has_data:
            raise PackDecodeError(
                'Failed to validate data checksum: pack has no data section (index pack)'
            )
        # data section checksum covers: header + index section + length vint + data
        digest = sha1()
        digest.update(self.data[:5])
        digest.update(self.index_section)
        digest.update(self.data_section[:-20])
        if digest.digest() != bytes(self.data_section[-20:]):
            raise PackDecodeError('Failed to validate data checksum: checksum mismatch')

    def extract_index_pack(self):
        """
        Extract the index pack from this pack.

        The index pack is the header plus the index section, which is a
        prefix of every pack: extracting from a full pack drops the data
        section, and an index pack extracts to itself.

        Returns:
            memoryview: Index pack bytes, a zero-copy slice of the pack
        """
        return self.data[:5 + len(self.index_section)]

    @cached_property
    def idx_info(self) -> "list[IdxInfo]":
        """
        Decode index_data into records, refinfo entries first then fileinfo.

        Returns:
            list[IdxInfo]: All records in the encoded order

        Raises:
            PackDecodeError: If index_data is malformed
        """
        data = self._index_part
        offset = 0
        len_refinfo, read = _decode('index data: counts', decode_vint, data[offset:])
        offset += read
        len_fileinfo, read = _decode('index data: counts', decode_vint, data[offset:])
        offset += read
        total = len_refinfo + len_fileinfo

        # path encoding
        prefix_comb, read = _decode('index data: prefix comb', decode_vlenint, data[offset:])
        offset += read
        _check_length('index data: prefix comb', len(prefix_comb), total)
        prefix_reuse, path_len = _decode(
            'index data: prefix comb', decode_prefix_comb, prefix_comb)
        suffix_comb, read = _decode('index data: suffix comb', decode_vlenint, data[offset:])
        offset += read
        _check_length('index data: suffix comb', len(suffix_comb), total)
        suffix_reuse, suffix_lookback = _decode(
            'index data: suffix comb', decode_suffix_comb, suffix_comb)
        path_bytes = sum(path_len)
        if offset + path_bytes > len(data):
            raise PackDecodeError(
                f'Failed to decode index data: path bytes out of range: '
                f'{offset + path_bytes} > {len(data)}'
            )
        path_data = data[offset:offset + path_bytes]
        offset += path_bytes

        # edit (fileinfo only)
        edits, read = _decode('index data: edits', decode_bit2, data[offset:])
        offset += read
        _check_length('index data: edits', len(edits), len_fileinfo)

        # source lookback (fileinfo, deleted files have none)
        non_deleted = sum(1 for edit in edits if edit != 2)
        lookbacks, read = _decode(
            'index data: source lookback', decode_vlenint, data[offset:])
        offset += read
        _check_length('index data: source lookback', len(lookbacks), non_deleted)
        source_lookbacks = []
        it_lookback = iter(lookbacks)
        for edit in edits:
            if edit == 2:
                source_lookbacks.append(0)
            else:
                source_lookbacks.append(next(it_lookback))

        # file meta (fileinfo, excluded D and C)
        non_dc = sum(
            1 for edit, lookback in zip(edits, source_lookbacks)
            if not (edit == 2 or (edit == 0 and lookback))
        )
        eols, read = _decode('index data: eol', decode_bit2, data[offset:])
        offset += read
        _check_length('index data: eol', len(eols), non_dc)
        modes, read = _decode('index data: mode', decode_bit2, data[offset:])
        offset += read
        _check_length('index data: mode', len(modes), non_dc)
        algos, read = _decode('index data: algo', decode_bit2, data[offset:])
        offset += read
        _check_length('index data: algo', len(algos), non_dc)

        # size (all refinfo + non-D non-C fileinfo)
        sizes, read = _decode('index data: size', decode_vlenint, data[offset:])
        offset += read
        _check_length('index data: size', len(sizes), len_refinfo + non_dc)

        # data_size diff (non-D non-C fileinfo with algo != 0)
        diffs, read = _decode('index data: data_size', decode_vlenint, data[offset:])
        offset += read

        # all index_data bytes must be consumed
        if offset != len(data):
            raise PackDecodeError(
                f'Failed to decode index data: {len(data) - offset} trailing bytes'
            )

        # decode paths in the encoded order: refinfo first, then fileinfo
        paths = self._decode_paths(
            path_data, prefix_reuse, path_len, suffix_reuse, suffix_lookback,
        )

        # build the record list with the minimal attributes (path) first,
        # the remaining attributes are filled in the passes below
        info_list = [IdxInfo(path=path) for path in paths]

        # fileinfo: edit, source lookback and source_path
        for i, info in enumerate(info_list[len_refinfo:]):
            edit = edits[i]
            lookback = source_lookbacks[i]
            info.edit = edit
            info.source_lookback = lookback
            # source_path is the path of the record this file references
            # (C / R / RM / zstd patch), resolved through source_lookback,
            # a 1-based lookback into the full record list
            if lookback:
                source_index = len_refinfo + i - lookback
                if source_index < 0:
                    raise PackDecodeError(
                        f'Failed to decode index data: source lookback out of range: '
                        f'{lookback} > {len_refinfo + i}, path={info.path}'
                    )
                info.source_path = info_list[source_index].path

        # meta of non-D non-C fileinfo
        non_dc = [
            info for info in info_list[len_refinfo:]
            if info.edit != 2 and not (info.edit == 0 and info.source_lookback)
        ]
        # check the attribute list lengths before assigning
        _check_length('index data: eol', len(non_dc), len(eols))
        _check_length('index data: mode', len(non_dc), len(modes))
        _check_length('index data: algo', len(non_dc), len(algos))
        _check_length('index data: size', len(sizes), len_refinfo + len(non_dc))

        for info, eol in zip(non_dc, eols):
            info.eol = eol
        for info, mode in zip(non_dc, modes):
            info.mode = mode
        for info, algo in zip(non_dc, algos):
            info.algo = algo
        for info, size in zip(info_list[:len_refinfo], sizes[:len_refinfo]):
            info.size = size
        # data_size starts as the full size, compressed files subtract the diff
        for info, size in zip(non_dc, sizes[len_refinfo:]):
            info.size = size
            info.data_size = size
        diff_infos = [info for info in non_dc if info.algo]
        _check_length('index data: data_size', len(diff_infos), len(diffs))
        for info, diff in zip(diff_infos, diffs):
            info.data_size -= diff

        # sha1: all refinfo + non-D non-C fileinfo with data_size > 0,
        # matching iter_sha1_data() which skips data_size == 0
        count_sha1 = len_refinfo + sum(1 for info in non_dc if info.data_size)
        if len(self._sha1_part) != count_sha1 * 20:
            raise PackDecodeError(
                f'Failed to decode sha1 part: decoded {len(self._sha1_part)} bytes, '
                f'expected {count_sha1 * 20}'
            )
        sha1s = iter(
            bytes(self._sha1_part[offset:offset + 20]).hex()
            for offset in range(0, len(self._sha1_part), 20)
        )
        for info in info_list[:len_refinfo]:
            info.sha1 = next(sha1s)

        # data: sha1 and data_start for files with data
        data_offset = 0
        # data_start is an offset into the full pack file, used directly
        # by range requests
        data_start = self._data_start
        for info in non_dc:
            if info.data_size:
                info.sha1 = next(sha1s)
                # offset in the full pack file, data can be indexed with
                # data_start and data_size directly on the pack bytes
                info.data_start = data_start + data_offset
                data_offset += info.data_size

        # copied: no data in pack, restore the attributes omitted by the
        # encoder from the source record (identical content)
        for i, info in enumerate(info_list[len_refinfo:], start=len_refinfo):
            if info.edit == 0 and info.source_lookback:
                source = info_list[i - info.source_lookback]
                info.size = source.size
                info.eol = source.eol
                info.mode = source.mode
                info.algo = source.algo
                info.data_size = source.data_size
                info.data_start = source.data_start
                info.sha1 = source.sha1

        self._len_refinfo = len_refinfo
        return info_list

    @staticmethod
    def _decode_paths(path_data, prefix_reuse, path_len, suffix_reuse, suffix_lookback):
        """
        Replay the path encoding: prefix reuse + remaining bytes + suffix reuse.

        Suffix references are 1-based lookbacks into the already decoded
        paths (refinfo first, then fileinfo), matching PathLookbackLCS.

        Args:
            path_data (memoryview): Concatenated remaining path bytes
            prefix_reuse (list[int]): Prefix lengths reused from previous path
            path_len (list[int]): Byte lengths of remaining path chunks
            suffix_reuse (list[int]): Suffix lengths reused from lookback path
            suffix_lookback (list[int]): 1-based lookback distances

        Returns:
            list[str]: Decoded full paths in encoded order

        Raises:
            PackDecodeError: If a suffix lookback is out of range
        """
        paths = []
        prev = ''
        offset = 0
        for i, length in enumerate(path_len):
            remaining = bytes(path_data[offset:offset + length]).decode('utf-8', errors='replace')
            offset += length
            lookback = suffix_lookback[i]
            if lookback:
                if lookback > i:
                    raise PackDecodeError(
                        f'Failed to decode paths: suffix lookback out of range: '
                        f'{lookback} > {i}'
                    )
                suffix = paths[i - lookback][-suffix_reuse[i]:]
            else:
                suffix = ''
            path = ''.join((prev[:prefix_reuse[i]], remaining, suffix))
            paths.append(path)
            prev = path
        return paths

    @staticmethod
    def apply_eol(content, eol):
        """
        Convert LF blob content to the checkout line ending.

        Git blobs always store normalized LF content for text files, the
        working tree file gets CRLF when the checkout rule says so. This
        converts the LF content into the working tree form.

        Args:
            content (bytes | memoryview): Blob content, usually LF
            eol (int): Line ending rule, 0 for LF, 1 for CRLF, 2 for binary

        Returns:
            bytes: Content with the checkout line ending applied
        """
        if eol == 1:
            # normalize any stray CRLF first, the blob should be LF
            content = bytes(content)
            return content.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        # LF (0) and binary (2) are written as-is
        return content

    def catdata(self, info) -> memoryview:
        """
        Get the raw bytes of this file from the data section.

        The data may be uncompressed, or lzma/zstd compressed -- no
        decompression is performed. See catfile() to get the content.

        Args:
            info (IdxInfo): Record with data_start / data_size

        Returns:
            memoryview: Raw bytes in the data section

        Raises:
            PackDecodeError: If the pack has no data section (index pack)
        """
        if not self._has_data:
            raise PackDecodeError(
                'Failed to read data: pack has no data section (index pack)'
            )
        return self.data[info.data_start:info.data_start + info.data_size]

    def catfile(self, info) -> memoryview:
        """
        Extract the working tree content of this file.

        The pack stores git blob content (LF normalized for text files).
        This method decompresses the data and applies the checkout line
        ending rule (see apply_eol), so the result is what the file
        should look like in the working tree: CRLF for eol == 1 files.

        Files stored raw (algo == 0) are returned as a zero-copy memoryview
        slice of the pack. lzma (algo == 1) and full zstd (algo == 2 with
        source_lookback == 0) data are decompressed automatically.

        zstd patch data (algo == 2 with source_lookback != 0) must be
        decompressed with the old file content, which this method cannot
        provide, so it raises PackDecodeError.

        Files without data (empty, deleted) return an empty memoryview.
        Copied files restore the data attributes of their source record,
        so they can be read directly like the source file.

        Args:
            info (IdxInfo): Record with data_start / data_size / algo

        Returns:
            memoryview: Working tree file content

        Raises:
            PackDecodeError: If algo is unknown, the data is a zstd patch
                that requires the old file, the pack has no data section
                (index pack), decompression fails, or the decoded content
                does not match the recorded size / sha1
        """
        content = self.catdata(info)
        return memoryview(self.decode_content(info, content))

    @staticmethod
    def decode_content(info, data):
        """
        Decompress the raw file data and check it against the record.

        The data is the raw content of the file in the full pack:
        uncompressed for algo == 0, lzma/zstd compressed otherwise.
        The decoded blob content must match the recorded size and
        sha1, then the checkout line ending is applied.

        Args:
            info (IdxInfo): Record of the file
            data (bytes | memoryview): Raw file data in the full pack

        Returns:
            bytes: Working tree file content

        Raises:
            PackDecodeError: If algo is unknown, the data is a zstd
                patch that requires the old file, decompression fails,
                or the decoded content does not match the recorded
                size / sha1
        """
        if info.algo != 0:
            data = PackDecodeBase._decompress(info, data)
        PackDecodeBase._check_content(info, data)
        return PackDecodeBase.apply_eol(data, info.eol)

    @staticmethod
    def _decompress(info, data, source=None):
        """
        Decompress the data of info, wrapping errors into PackDecodeError.

        algo == 1 data is lzma compressed, algo == 2 data is zstd
        compressed. source provides the old file content as the zstd
        dictionary for zstd patch data, callers that have it can pass it.

        Args:
            info (IdxInfo): Record being decompressed
            data (memoryview): Raw data of the file from catdata
            source (bytes | memoryview, optional): Old file content as the
                zstd dictionary for zstd patch data. Defaults to None.

        Returns:
            bytes: Decompressed content

        Raises:
            PackDecodeError: If the data is a zstd patch without source,
                or algo is unknown or decompression fails
        """
        if info.algo == 2 and info.source_lookback and source is None:
            raise PackDecodeError(
                f'Failed to decompress {info.path}: zstd patch data '
                f'requires the old file content'
            )
        try:
            if info.algo == 1:
                return lzma_decompress(data)
            if info.algo == 2:
                return zstd_decompress(data, source)
        except Exception as e:
            raise PackDecodeError(f'Failed to decompress {info.path}: {e}') from e
        raise PackDecodeError(f'Failed to decompress {info.path}: unknown algo {info.algo}')

    @staticmethod
    def _check_content(info, content):
        """
        Verify decoded content against the recorded size and sha1.

        Checked in git blob form (LF normalized), before apply_eol converts
        to the working tree form: size is the blob length and sha1 is the
        blob sha1. Records without data (empty, deleted) have size 0 and an
        empty sha1, which always pass.

        Args:
            info (IdxInfo): Record being decoded
            content (bytes | memoryview): Decoded blob content

        Raises:
            PackDecodeError: If the size or sha1 mismatch
        """
        if len(content) != info.size:
            raise PackDecodeError(
                f'Failed to decode {info.path}: size mismatch: '
                f'decoded {len(content)} bytes, expected {info.size}'
            )
        if info.sha1 and sha1(content).hexdigest() != info.sha1:
            raise PackDecodeError(
                f'Failed to decode {info.path}: sha1 mismatch: '
                f'decoded {sha1(content).hexdigest()}, expected {info.sha1}'
            )

    @cached_property
    def refinfo(self) -> "dict[str, IdxInfo]":
        """
        Old file records, empty in full pack.

        Returns:
            dict[str, IdxInfo]: {filepath: IdxInfo}

        Raises:
            PackDecodeError: If the records contain duplicate paths
        """
        info = self.idx_info
        return self._to_dict(info[:self._len_refinfo], 'refinfo')

    @cached_property
    def fileinfo(self) -> "dict[str, IdxInfo]":
        """
        New file records.

        Returns:
            dict[str, IdxInfo]: {filepath: IdxInfo}

        Raises:
            PackDecodeError: If the records contain duplicate paths
        """
        info = self.idx_info
        return self._to_dict(info[self._len_refinfo:], 'fileinfo')

    @staticmethod
    def _to_dict(records, section):
        """
        Index records by path, rejecting duplicate paths.

        A well-formed pack never contains the same path twice, a duplicate
        here means the pack is corrupt: silently overwriting would lose a
        record.

        Args:
            records (list[IdxInfo]): Records to index
            section (str): Section name for the error message

        Returns:
            dict[str, IdxInfo]: {filepath: IdxInfo}

        Raises:
            PackDecodeError: If a path appears more than once
        """
        out = {}
        for info in records:
            if info.path in out:
                raise PackDecodeError(
                    f'Failed to decode {section}: duplicate path {info.path!r}'
                )
            out[info.path] = info
        return out
