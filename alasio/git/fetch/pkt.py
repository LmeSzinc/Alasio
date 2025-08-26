from collections import deque

from alasio.logger import logger


async def aparse_pkt_line(stream_iterator):
    """
    Asynchronously parses a pkt-line formatted stream.

    This async generator consumes an async iterator of byte chunks (like one
    from httpx.aiter_raw() or a trio stream) and yields individual pkt-line
    data payloads. It uses a `bytearray` for efficient buffering.

    Args:
        stream_iterator: An async iterator yielding byte chunks.

    Yields:
        A `bytes` object for each data line, or an empty `bytes` object for a flush-pkt.
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
                yield b''
                del buffer[:4]
                continue

            # line length
            line_length = int(length_hex, 16)
            if len_buffer < line_length:
                break

            line_data = buffer[4:line_length]
            del buffer[:line_length]
            yield bytes(line_data)


def parse_pkt_line(content):
    """
    Synchronously parses a pkt-line formatted content.

    Args:
        content (bytes):

    Yields:
        bytes
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
        try:
            line_length = int(length_hex, 16)
        except ValueError:
            logger.warning(f'fetch_refs: Invalid pkt line length: "{length_hex}"')
            break
        end = index + line_length
        line = content[start: end]
        index = end
        yield line


async def aparse_packfile_stream(stream_iterator, buffer_size=262144) -> int:
    """
    A generic, buffered packfile stream handler.

    This helper reads from a pkt-line stream, demultiplexes side-band-64k
    data, writes packfile data to a file using an in-memory buffer for
    performance, and calls a progress callback. It uses a list and `b''.join`
    for efficient buffer management.

    Args:
        stream_iterator: An async iterator yielding pkt-line data payloads.
        buffer_size (int): 256KB default write buffer size

    Yields:
        bytes: data chunk
    """
    buffer_list = deque()
    current_buffer_len = 0

    async for line in stream_iterator:
        if not line:
            continue

        channel = line[0:1]
        content = line[1:]

        # Packfile data
        if channel == b'\x01':
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
        # ACK 4b6229f1236825f353b5d7154aed834622e9a2b7 common\n
        elif channel == b'A':
            pass

        # this shouldn't happen
        else:
            logger.warning(f'Unexpected pkt line, channel: {channel}, content={content}')

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
    Create a pkt-line formatted byte string from a string.

    Args:
        data (str): The string data to encode, without \n at string end

    Returns:
        bytes: pkt-line, {length}{data}\n
    """
    data = data.encode('utf-8')
    # len(data) + len(length) + len('\n')
    length = len(data) + 5
    length = f'{length:04x}'.encode('utf-8')
    return b''.join([length, data, b'\n'])


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
        self.append(create_pkt_line(line))

    def add_delimiter(self):
        self.append(b'0000')

    def add_done(self):
        self.append(create_pkt_line('done'))

    def add_have(self, sha1):
        """
        Args:
            sha1 (str):
        """
        self.append(create_pkt_line(f'have {sha1}'))

    def add_deepen(self, deepen):
        """
        Args:
            deepen (int):
        """
        self.append(create_pkt_line(f'deepen {deepen}'))
