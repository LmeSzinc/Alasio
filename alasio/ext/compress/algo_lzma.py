import lzma
import struct

# the two size fields of a chunk header: the unpacked one and the packed one, each
# 16 bits big endian. The unpackers are compiled once, they are the hot part of
# lzma2_uncompressed_size()
UNPACK_UNPACKED = struct.Struct('>H').unpack_from
UNPACK_CHUNK = struct.Struct('>HH').unpack_from

# the largest dictionary size of a compressed content, 2^26 = 64MB, the cap of
# _lzma_dictsize and the dictionary of a stream of another compressor rule
MAX_DICT_SIZE = 1 << 26


def _lzma_dictsize(length, max_dict_size=None):
    """
    Auto LZMA dictionary size depends on content length
    4KB to 64MB

    Args:
        length (int): Data length to compress.
        max_dict_size (int, optional): Maximum dictionary size cap.
            If not a power of 2, capped to the nearest power of 2 less than it.
            If less than 4096, treated as 4096.

    Returns:
        int: Chosen dictionary size.
    """
    # 2^12 = 4096 (min), 2^26 = 67108864 = 64MB (max)
    max_exp = 26
    min_exp = 12

    if max_dict_size is not None:
        cap_exp = max_dict_size.bit_length() - 1
        if cap_exp < min_exp:
            cap_exp = min_exp
        elif cap_exp > max_exp:
            cap_exp = max_exp
    else:
        cap_exp = max_exp

    if length > 0:
        exp = (length - 1).bit_length()
    else:
        exp = min_exp

    if exp < min_exp:
        exp = min_exp
    if exp > cap_exp:
        exp = cap_exp

    return 1 << exp


def lzma_compress(data, max_dict_size=None):
    """
    Compress data using lzma with the best compression ratio

    The dictionary size is derived from the content length (see
    _lzma_dictsize), so the stream of a content is reproducible and a decoder
    can pick the same dictionary from the length of the content.

    Args:
        data (bytes | memoryview): Data to compress
        max_dict_size (int, optional): Maximum dictionary size cap, 4096 ~ 67108864

    Returns:
        bytes:
    """
    # dict size larger than data length is meaning less
    # so having auto adjusted dict size can speed up compressor initialization and reduce memory usage
    dictsize = _lzma_dictsize(len(data), max_dict_size=max_dict_size)
    my_filters = [
        {
            "id": lzma.FILTER_LZMA2,
            "dict_size": dictsize,  # 根据文件大小动态计算的字典
            "preset": 9,  # 基础底子设为 9
            "nice_len": 273,  # 压榨最后一点空间，寻找最长匹配
            "mf": lzma.MF_BT4,  # 确保使用二叉树匹配
        }
    ]
    compressed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=my_filters, check=lzma.CHECK_NONE)
    return compressed


def lzma2_uncompressed_size(data):
    """
    Read the uncompressed size of a raw LZMA2 stream from its chunk headers.

    A raw LZMA2 stream is a chain of chunks, each one headed by a control byte
    and the packed and unpacked sizes of the chunk (see the LZMA2 decoder of
    liblzma, lzma2_decoder.c). Walking the headers gives the size of the whole
    stream without decompressing a byte and without allocating a dictionary:
    the chunks are skipped by the size they declare.

    The header of a chunk, from the control byte:

        control == 0x00             end of the stream
        control >= 0x80             LZMA chunk
            unpacked = (control % 32) * 65536 + be16(1) + 1
            packed   = be16(3) + 1
            properties (1 byte) when control >= 0xC0
        control == 0x01 / 0x02      uncompressed chunk
            packed = unpacked = be16(1) + 1

    The offsets of be16 above are relative to the byte behind the control: the
    walk consumes the control first, so the reads need no + 1 of their own. The
    jump over a chunk is the header behind the control (4 bytes, 5 when the
    properties are present) with the + 1 of the packed field folded into it.

    The bounds of the stream are not checked: the reads raise IndexError and
    struct.error past its end, and the handlers report them as the failures
    they are. The checks are the whole cost of the loop on a stream of many
    chunks, and a valid stream never takes a handler.

    Args:
        data (bytes | bytearray | memoryview): Raw LZMA2 stream

    Returns:
        int: Uncompressed size of the stream

    Raises:
        ValueError: If the stream is truncated, holds an invalid control byte,
            or has no end marker
    """
    size = 0
    offset = 0
    end = len(data)
    try:
        while True:
            control = data[offset]
            if control == 0x00:
                return size
            offset += 1
            if control >= 0x80:
                low, packed = UNPACK_CHUNK(data, offset)
                size += (control % 32) * 65536 + low + 1
                # the header behind the control (4 bytes, 5 with properties)
                # and the payload of the packed field plus one
                offset += (6 if control >= 0xC0 else 5) + packed
            else:
                if control > 0x02:
                    raise ValueError(
                        f'Invalid LZMA2 control byte 0x{control:02x} at {offset - 1}')
                unpacked = UNPACK_UNPACKED(data, offset)[0] + 1
                size += unpacked
                offset += 2 + unpacked
    except IndexError:
        # the read past the stream: a chunk that declares more bytes than the
        # stream holds, or a stream without its end marker
        if offset > end:
            raise ValueError(f'LZMA2 chunk out of range at {offset}') from None
        raise ValueError('LZMA2 stream has no end marker') from None
    except struct.error:
        raise ValueError(f'Truncated LZMA2 chunk header at {offset - 1}') from None


def lzma_decompress(data, length=None):
    """
    Decompress data compressed by lzma_compress

    A raw LZMA2 stream does not state its dictionary size: the chunk header is
    the control byte, the unpacked size, the packed size and the lc/lp/pb
    properties, nothing else (the dictionary size lives in the block header of
    an .xz container, not in the raw stream). The decoder is given one instead,
    and liblzma allocates it: a live decoder holds a buffer of
    dict_size + 608 bytes (lz_decoder.c), whatever the content it decodes, and
    a stream coded with a larger dictionary fails with "Corrupt input data"
    when a match reaches back past the buffer.

    The dictionary is therefore the one the encoder used, derived the way it
    was derived: from the length of the content (see _lzma_dictsize). A caller
    that knows the length passes it and the stream is decoded once; the caller
    that does not know it reads the length from the chunk headers of the stream
    (see lzma2_uncompressed_size), which costs no decode and no dictionary.

    The derived dictionary always fits its own stream: a match distance is
    smaller than the content, and the dictionary of a length is never smaller
    than the length itself (up to the cap, which the encoder respects too).

    Args:
        data (bytes | memoryview): Compressed data.
        length (int, optional): Uncompressed length of the content, e.g. the
            size of a pack record. Defaults to None, read from the stream.

    Returns:
        bytes: Decompressed data.

    Raises:
        ValueError: If the data is not an LZMA2 stream, or its size cannot be
            read (see lzma2_uncompressed_size)
        lzma.LZMAError: If the data cannot be decoded
    """
    if length is None:
        length = lzma2_uncompressed_size(data)
    dict_size = _lzma_dictsize(length)
    try:
        return lzma.decompress(
            data, format=lzma.FORMAT_RAW,
            filters=[{'id': lzma.FILTER_LZMA2, 'dict_size': dict_size}],
        )
    except lzma.LZMAError:
        if dict_size == MAX_DICT_SIZE:
            raise
    # The length is not the one of the content, e.g. a pack record that is
    # damaged: the derived dictionary is too small for the stream, and the
    # largest one, the one every stream of lzma_compress fits in, decodes it.
    # The caller checks the content against its record either way.
    return lzma.decompress(
        data, format=lzma.FORMAT_RAW,
        filters=[{'id': lzma.FILTER_LZMA2, 'dict_size': MAX_DICT_SIZE}],
    )
