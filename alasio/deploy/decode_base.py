
from hashlib import sha1

from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext.algorithm.bit2coding import decode_bit2
from alasio.ext.algorithm.pathlen_coding import decode_prefix_comb, decode_suffix_comb
from alasio.ext.algorithm.vint import decode_vint
from alasio.ext.algorithm.vlenint import decode_vlenint
from alasio.ext.cache import cached_property


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
            end of its checksum digest.
        refinfo (list[IdxInfo]): Old file records (empty in full pack).
        fileinfo (list[IdxInfo]): New file records.
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

        # data section
        data_offset = index_end
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
            PackDecodeError: If any checksum mismatches
        """
        # index section checksum covers: header + length vint + parts
        digest = sha1()
        digest.update(self.data[:5])
        digest.update(self.index_section[:-20])
        if digest.digest() != bytes(self.index_section[-20:]):
            raise PackDecodeError('Failed to validate index checksum: checksum mismatch')

        # data section checksum covers: header + index section + length vint + data
        digest = sha1()
        digest.update(self.data[:5])
        digest.update(self.index_section)
        digest.update(self.data_section[:-20])
        if digest.digest() != bytes(self.data_section[-20:]):
            raise PackDecodeError('Failed to validate data checksum: checksum mismatch')

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
        prefix_reuse, path_len = decode_prefix_comb(prefix_comb)
        suffix_comb, read = _decode('index data: suffix comb', decode_vlenint, data[offset:])
        offset += read
        _check_length('index data: suffix comb', len(suffix_comb), total)
        suffix_reuse, suffix_lookback = decode_suffix_comb(suffix_comb)
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
        count_diff = sum(1 for algo in algos if algo != 0)
        diffs, read = _decode('index data: data_size', decode_vlenint, data[offset:])
        offset += read
        _check_length('index data: data_size', len(diffs), count_diff)

        # all index_data bytes must be consumed
        if offset != len(data):
            raise PackDecodeError(
                f'Failed to decode index data: {len(data) - offset} trailing bytes'
            )

        # collect non-D non-C fileinfo meta, data_size = size - diff
        metas = []
        i_meta = 0
        i_size = len_refinfo
        i_diff = 0
        for edit, lookback in zip(edits, source_lookbacks):
            if edit == 2 or (edit == 0 and lookback):
                # D (deleted) and C (copied) have no meta in pack
                metas.append(None)
                continue
            algo = algos[i_meta]
            size = sizes[i_size]
            if algo:
                data_size = size - diffs[i_diff]
                i_diff += 1
            else:
                # raw files store the full content, data_size == size
                data_size = size
            metas.append((eols[i_meta], modes[i_meta], algo, size, data_size))
            i_meta += 1
            i_size += 1

        # sha1: all refinfo + non-D non-C fileinfo with data_size > 0,
        # matching iter_sha1_data() which skips data_size == 0
        count_sha1 = len_refinfo + sum(1 for meta in metas if meta and meta[4])
        if len(self._sha1_part) != count_sha1 * 20:
            raise PackDecodeError(
                f'Failed to decode sha1 part: decoded {len(self._sha1_part)} bytes, '
                f'expected {count_sha1 * 20}'
            )
        sha1s = iter(
            bytes(self._sha1_part[i * 20:(i + 1) * 20]).hex()
            for i in range(count_sha1)
        )

        # decode paths in the encoded order: refinfo first, then fileinfo
        paths = self._decode_paths(
            path_data, prefix_reuse, path_len, suffix_reuse, suffix_lookback,
        )

        # build refinfo entries
        info_list = []
        for i in range(len_refinfo):
            info = IdxInfo(path=paths[i], size=sizes[i])
            info.sha1 = next(sha1s)
            info_list.append(info)

        # build fileinfo entries, data_start is the offset in the pack file
        data_offset = 0
        for i in range(len_fileinfo):
            path = paths[len_refinfo + i]
            edit = edits[i]
            lookback = source_lookbacks[i]
            meta = metas[i]
            if edit == 2:
                # deleted: no meta, no size, no sha1, no data
                info = IdxInfo(path=path, edit=edit)
            elif meta is None:
                # copied: reuse the info of the source file, no data in pack
                info = IdxInfo(path=path, edit=edit, source_lookback=lookback)
            else:
                eol, mode, algo, size, data_size = meta
                info = IdxInfo(
                    path=path, edit=edit, eol=eol, mode=mode, algo=algo,
                    size=size, source_lookback=lookback, data_size=data_size,
                )
                if data_size:
                    info.sha1 = next(sha1s)
                    # offset in the pack file, data can be indexed with
                    # data_start and data_size directly on the pack bytes
                    info.data_start = self._data_start + data_offset
                    data_offset += data_size
            info_list.append(info)

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
            remaining = bytes(path_data[offset:offset + length]).decode('utf-8')
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
            path = prev[:prefix_reuse[i]] + remaining + suffix
            paths.append(path)
            prev = path
        return paths

    @cached_property
    def refinfo(self) -> "list[IdxInfo]":
        """
        Old file records, empty in full pack.

        Returns:
            list[IdxInfo]: refinfo entries
        """
        info = self.idx_info
        return info[:self._len_refinfo]

    @cached_property
    def fileinfo(self) -> "list[IdxInfo]":
        """
        New file records.

        Returns:
            list[IdxInfo]: fileinfo entries
        """
        info = self.idx_info
        return info[self._len_refinfo:]
