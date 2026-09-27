import zstandard as zstd

# Below this input size a higher level is not reliably smaller: zstd levels are
# not monotone on small inputs, level 22 can emit a few bytes more than level 3
# for a small file. Level 3 is then tried as well and the smaller output wins,
# which costs one fast compression of at most this many bytes.
SMALL_INPUT_SIZE = 64 * 1024


def _zstd_window_log(level, data, source):
    """
    Window log for a compression, at least what the level asks for

    The level defines the search window. zstd only writes the content size into
    the frame header of a single segment frame, which requires the window to
    cover the whole input, and zstd_decompress() reads that content size to
    pre-allocate. Sizes above the window of the level therefore raise the
    window instead of producing a frame the decoder cannot size.

    Args:
        level (int): Compression level, 1-22
        data (bytes | memoryview): Data to compress
        source (bytes | memoryview): Dictionary of a patch-from compression

    Returns:
        int: Window log, 2 ** window_log >= max(len(data), len(source))
    """
    window_log = zstd.ZstdCompressionParameters.from_level(level).window_log
    if len(data).bit_length() > window_log:
        window_log = len(data).bit_length()
    if source is not None and len(source).bit_length() > window_log:
        window_log = len(source).bit_length()
    return window_log


def _zstd_compress_with_level(data, dict_data, source, level, magicless):
    """
    One compression at one level

    Args:
        data (bytes | memoryview): Data to compress
        dict_data (zstd.ZstdCompressionDict | None): Dictionary of a patch-from compression
        source (bytes | memoryview): Old data of a patch-from compression, for the window
        level (int): Compression level, 1-22
        magicless (bool): Whether to omit the 4-byte magic header

    Returns:
        bytes: Compressed data
    """
    # from_level() derives the real parameters (window, chains, strategy) from the
    # level. A plain ZstdCompressionParameters() leaves them at their defaults, and
    # ZstdCompressor(level=...) is ignored as soon as compression_params is given,
    # which used to run every caller at the default level whatever it asked for.
    params = zstd.ZstdCompressionParameters.from_level(
        level,
        format=zstd.FORMAT_ZSTD1_MAGICLESS if magicless else zstd.FORMAT_ZSTD1,
        # no checksum because we have our own sha1 checking on old files and new files
        write_checksum=False,
        # write content size so decompressor can pre-allocate memory
        write_content_size=True,
        # no dict_id because it's meaningless in `zstd --patch-from`, which dict_id is always 0
        write_dict_id=False,
        window_log=_zstd_window_log(level, data, source),
    )
    compressor = zstd.ZstdCompressor(
        dict_data=dict_data,
        compression_params=params,
    )
    return compressor.compress(data)


def zstd_compress(data, source=None, level=22, magicless=True):
    """
    Compress data using zstd with the best compression ratio

    Args:
        data (bytes | memoryview): Data to compress
        source (bytes | memoryview): Old file data as zstd dictionary to compress like `zstd --patch-from`
        level (int): Compression level, 1-22. Defaults to 22.
        magicless (bool): Whether to omit the 4-byte magic header. Defaults to True.

    Returns:
        bytes:
    """
    if source is None:
        dict_data = None
    else:
        dict_data = zstd.ZstdCompressionDict(source)

    out = _zstd_compress_with_level(data, dict_data, source, level, magicless)
    # small inputs: the level above 3 is not always the smaller one, keep the
    # level 3 output when it wins
    if level > 3 and len(data) <= SMALL_INPUT_SIZE:
        small = _zstd_compress_with_level(data, dict_data, source, 3, magicless)
        if len(small) < len(out):
            out = small
    return out


def zstd_decompress(data, source=None):
    """
    Args:
        data (bytes | memoryview): Compressed data, accepts any bytes-like
        source (bytes | memoryview): Old file data as zstd dictionary to
            decompress like `zstd -d --patch-from`

    Returns:
        bytes:
    """
    if source is None:
        dict_data = None
    else:
        dict_data = zstd.ZstdCompressionDict(source)

    # Auto-detect format: if data starts with zstd magic header, use default format,
    # otherwise treat as magicless (FORMAT_ZSTD1_MAGICLESS).
    if data[:len(zstd.FRAME_HEADER)] == zstd.FRAME_HEADER:
        fmt = zstd.FORMAT_ZSTD1
    else:
        fmt = zstd.FORMAT_ZSTD1_MAGICLESS

    decompressor = zstd.ZstdDecompressor(
        dict_data=dict_data,
        format=fmt,
    )
    out = decompressor.decompress(data)
    return out
