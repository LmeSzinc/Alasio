from collections import deque
from hashlib import sha1

import trio

from alasio.ext.path.atomic import CHUNK_SIZE
from alasio.git.file.exception import PackBroken, PktLineBroken
from alasio.logger import logger

# Bytes to read from the connection at once
RECEIVE_SIZE = 65536

# Bytes of the sha1 that closes a pack file: everything before them is summed
# into it, so a transfer cut short cannot pass for a complete pack
PACK_CHECKSUM_SIZE = 20


async def receive_chunks(stream, size=RECEIVE_SIZE):
    """
    Yield what the server sends until the stream ends.

    The end of a git:// answer is not always a clean close: a server may reset
    the connection right after the last byte of the pack. `git daemon` on
    windows does exactly that, and a reset is the end of the stream here, not a
    failure.

    A reset is not proof that every byte arrived: windows discards the bytes a
    reset finds unread in the receive buffer, so the pack may be cut short
    without any error on the wire. A truncated pack is detected by its own
    trailing sha1 (see aparse_packfile_stream), and the client of the transfer
    runs it again on a fresh connection.

    Args:
        stream (trio.abc.ReceiveStream): The connection
        size (int): Bytes to read at once. Defaults to RECEIVE_SIZE.

    Yields:
        bytes: One chunk of the answer
    """
    while True:
        try:
            chunk = await stream.receive_some(size)
        except (trio.BrokenResourceError, trio.ClosedResourceError):
            # Reset by the peer, the stream is over
            return
        if not chunk:
            return
        yield chunk


def _feed_pack_checksum(hasher, tail, chunk):
    """
    Feed one chunk of pack bytes to the running checksum.

    The last PACK_CHECKSUM_SIZE bytes of a pack are the sha1 of everything
    before them, so the last bytes read have to be kept out of the hasher until
    the end of the stream says whether they are the checksum or pack data.
    Keeping only that tail means the pack is never held twice in memory.

    Args:
        hasher: Checksum of the bytes before the tail
        tail (bytes): Last bytes of the pack read so far, at most
            PACK_CHECKSUM_SIZE
        chunk (bytes): Next bytes of the pack

    Returns:
        tuple: The hasher, and the new tail of at most PACK_CHECKSUM_SIZE bytes
    """
    if tail:
        chunk = tail + chunk
    if len(chunk) <= PACK_CHECKSUM_SIZE:
        return hasher, chunk
    hasher.update(memoryview(chunk)[:-PACK_CHECKSUM_SIZE])
    return hasher, chunk[-PACK_CHECKSUM_SIZE:]


async def aparse_pkt_line(stream_iterator):
    """
    Asynchronously parses a pkt-line formatted stream.

    This async generator consumes an async iterator of byte chunks (like one
    from httpx2.aiter_raw() or a trio stream) and yields individual pkt-line
    data payloads. It uses a `bytearray` for efficient buffering.

    Args:
        stream_iterator: An async iterator yielding byte chunks.

    Yields:
        A `bytes` object for each data line, or an empty `bytes` object for a flush-pkt.

    Raises:
        PktLineBroken: If the stream ends in the middle of a line, which is a
            transfer cut short, not the end of the pkt-line stream
    """
    buffer = bytearray()
    async for chunk in stream_iterator:
        buffer.extend(chunk)
        while True:
            len_buffer = len(buffer)
            if len_buffer < 4:
                break

            # get next line
            length_hex = buffer[:4]
            if length_hex == b'0000':
                yield b''  # flush-pkt
                del buffer[:4]
                continue
            elif length_hex == b'0001':
                yield b'\x01'  # delimiter-pkt (v2)
                del buffer[:4]
                continue
            elif length_hex == b'0002':
                yield b'\x02'  # response-end-pkt (v2)
                del buffer[:4]
                continue

            # line length
            line_length = int(length_hex, 16)
            if len_buffer < line_length:
                break

            line_data = buffer[4:line_length]
            del buffer[:line_length]
            yield bytes(line_data)

    if buffer:
        raise PktLineBroken(
            f'pkt-line stream ended in the middle of a line: {len(buffer)} bytes left')


def parse_pkt_line(content):
    """
    Synchronously parses a pkt-line formatted content.

    Args:
        content (bytes):

    Yields:
        bytes

    Raises:
        PktLineBroken: If the content ends in the middle of a line
    """
    index = 0
    while True:
        start = index + 4
        length_hex = content[index:start]
        if not length_hex:
            break
        if length_hex == b'0000':
            index += 4
            continue
        elif length_hex == b'0001':
            yield b'\x01'  # delimiter-pkt (v2)
            index += 4
            continue
        elif length_hex == b'0002':
            yield b'\x02'  # response-end-pkt (v2)
            index += 4
            continue
        if len(length_hex) < 4:
            raise PktLineBroken(
                f'pkt-line stream ended in the middle of a length header: {length_hex!r}')
        try:
            line_length = int(length_hex, 16)
        except ValueError:
            logger.warning(f'fetch_refs: Invalid pkt line length: "{length_hex}"')
            break
        end = index + line_length
        if end > len(content):
            raise PktLineBroken(
                f'pkt-line stream ended in the middle of a line: {len(content) - start} of '
                f'{line_length - 4} bytes of the payload arrived')
        line = content[start: end]
        index = end
        yield line


async def aparse_packfile_stream(stream_iterator, buffer_size=CHUNK_SIZE) -> int:
    """
    A generic, buffered packfile stream handler.

    This helper reads from a pkt-line stream, demultiplexes side-band-64k
    data, writes packfile data to a file using an in-memory buffer for
    performance, and calls a progress callback. It uses a list and `b''.join`
    for efficient buffer management.

    The trailing sha1 of the pack is checked while the bytes stream, so a pack
    that arrived cut short (e.g. the reset of a server that dropped the bytes
    still unread in the receive buffer) or damaged by the transfer raises
    instead of being handed to the caller: every caller would otherwise have to
    check the pack itself, and a missing check leaves a broken pack on the disk
    that fails much later, when something reads it.

    Args:
        stream_iterator: An async iterator yielding pkt-line data payloads.
        buffer_size (int): 256KB default write buffer size

    Yields:
        bytes: data chunk

    Raises:
        PackBroken: If the stream ends before the pack is complete, or the
            trailing sha1 does not match the bytes received
    """
    buffer_list = deque()
    current_buffer_len = 0
    hasher = sha1()
    tail = b''

    async for line in stream_iterator:
        if not line:
            continue

        channel = line[0:1]
        content = line[1:]

        # Packfile data
        if channel == b'\x01':
            hasher, tail = _feed_pack_checksum(hasher, tail, content)
            buffer_list.append(content)
            current_buffer_len += len(content)

            if current_buffer_len >= buffer_size:
                yield b''.join(buffer_list)
                buffer_list = []
                current_buffer_len = 0

        # Progress information
        elif channel == b'\x02':
            # Flush buffer before printing progress
            if buffer_list:
                yield b''.join(buffer_list)
                buffer_list = []
                current_buffer_len = 0
            # Do we need to print progress?
            # progress content are like the follows,:

            # b'Enumerating objects: 118, done.\n'
            # b'Counting objects:   1% (1/71)\rCounting objects:   2% (2/71)\rCounting objects:   4% (3/71)\r'
            # b'Counting objects:   5% (4/71)\rCounting objects:   7% (5/71)\rCounting'
            # b' objects:  11% (8/71)\rCounting objects:  12% (9/71)\rCounting objec'
            # b'ts:  16% (12/71)\rCounting objects:  18% (13/71)\r'
            # b'Counting objects:  19% (14/71)\rCounting objects:  21% (15/71)\r'
            # b'Counting objects:  22% (16/71)\r'
            # ...
            # b'Compressing objects: 100% (40/40)\r'
            # b'Compressing objects: 100% (40/40), done.\n'
            # b'Total 118 (delta 34), reused 31 (delta 31), pack-reused 47 (from 1)\n'

            # if on_progress:
            #     on_progress(content.decode(errors='ignore').strip())

        # Error information
        elif channel == b'\x03':
            content = content.decode(errors='replace').strip()
            logger.error(f'Remote error: {content}')
        # ACK/NAK line during negotiation end
        elif channel in [b'A', b'N']:
            # A for ACK, N for NAK
            pass

        # this shouldn't happen
        else:
            logger.warning(f'Unexpected pkt line, channel: {channel}, content={content}')

    # The checksum is checked before the last chunk is handed over: a caller
    # that never sees a broken pack cannot write it to the disk
    if len(tail) != PACK_CHECKSUM_SIZE:
        raise PackBroken(
            f'Pack is truncated: {len(tail)} bytes where the trailing sha1 is expected')
    if hasher.digest() != tail:
        raise PackBroken(
            f'Pack checksum mismatch: computed={hasher.hexdigest()}, received={tail.hex()}')

    # Flush any remaining data in the buffer
    if buffer_list:
        yield b''.join(buffer_list)


async def agather_bytes(stream_iterator):
    """
    Gather all to a bytes object

    Args:
        stream_iterator: An async iterator yielding bytes or memoryview

    Returns:
        bytes:
    """
    content = deque()
    async for data in stream_iterator:
        content.append(data)
    return b''.join(content)


def create_pkt_line(data):
    """
    Create a standard pkt-line formatted byte string.
    Format: <4-byte hex length><data>
    Length includes the 4-byte header.

    Args:
        data (str | bytes): The data to encode.

    Returns:
        bytes: The pkt-line.
    """
    if isinstance(data, str):
        data = data.encode('utf-8')

    # Standard pkt-line length is len(data) + 4
    length = len(data) + 4
    length_hex = f'{length:04x}'.encode('utf-8')
    return b''.join([length_hex, data])


class FetchPayload(deque):
    def build(self):
        """
        Returns:
            bytes:
        """
        return b''.join(self)

    def add_line(self, line):
        """
        Args:
            line (str):
        """
        if not line.endswith('\n'):
            line += '\n'
        self.append(create_pkt_line(line))

    def add_delimiter(self):
        self.append(b'0000')

    def add_done(self):
        self.append(create_pkt_line('done\n'))

    def add_have(self, sha1):
        """
        Args:
            sha1 (str):
        """
        self.append(create_pkt_line(f'have {sha1}\n'))

    def add_deepen(self, deepen):
        """
        Args:
            deepen (int):
        """
        self.append(create_pkt_line(f'deepen {deepen}\n'))
