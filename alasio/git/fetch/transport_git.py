import trio

from alasio.ext.path.atomic import atomic_write
from alasio.git.fetch.pkt import FetchPayload, agather_bytes, aparse_packfile_stream, aparse_pkt_line, create_pkt_line, parse_pkt_line
from alasio.git.fetch.transport import BaseTransport
from alasio.logger import logger


class GitTransport(BaseTransport):
    """Transport implementation for Git protocol using TCP."""

    async def fetch_refs(self):
        """
        Returns:
            dict[bytes, bytes]:
                key: sha1
                value: ref like refs/heads/bug_fix, refs/heads/v2021.10.24, refs/pull/1007/head
        """
        host = self.arguments.repo_url.host
        port = self.arguments.repo_url.port or 9418
        path = self.arguments.repo_url.path
        logger.info(f'fetch_refs: git://{host}:{port}{path}')

        # Connect and send handshake
        stream = await trio.open_tcp_stream(host, port)
        handshake = f'git-upload-pack {path}\0host={host}\0'
        await stream.send_all(create_pkt_line(handshake))

        # Read refs
        content = bytearray()
        while True:
            chunk = await stream.receive_some(4096)
            if not chunk:
                break
            content.extend(chunk)
            if content.endswith(b'0000'):
                break

        await stream.aclose()

        logger.debug(f'Received {len(content)} bytes from server')
        logger.debug(f'First 200 bytes: {bytes(content[:200])!r}')

        out = {}
        for line in parse_pkt_line(bytes(content)):
            # parse line, which might be :
            # d5f854cedd666de9a99ebacbd3fb47971afc4271 HEAD\x00multi_ack thin-pack ...
            # cbba8f024a69c24380efe82b3b0ec3c736d01dfb refs/heads/bug_fix\n
            # 8b63ab79e956b90805dff2167448a72262fe50eb refs/heads/v2021.10.24\n
            # b9f14832259b2b093a03fdfd5c611baceea7c926 refs/pull/1007/head\n
            # b408a075f13681edd44fcbb17d452bdd8aaf67aa refs/tags/v2020.04.08\n
            sha1, sep, rest = line.strip().partition(b' ')
            if not sep:
                continue
            # Extract ref (may have capabilities after \0)
            ref = rest.split(b'\0')[0]
            if not ref.startswith(b'refs/'):
                continue
            # validate
            if len(sha1) != 40:
                logger.warning(f'fetch_refs: Unexpected sha1 length "{sha1}"')
                continue
            # set
            out[sha1] = ref

        return out

    async def fetch_pack_v1(self, payload: FetchPayload, output_file=None):
        """
        Args:
            payload (FetchPayload):
            output_file (str): Write into output_file directly
                If output_file is None, return pack file data

        Returns:
            bytes | None:
        """
        host = self.arguments.repo_url.host
        port = self.arguments.repo_url.port or 9418
        path = self.arguments.repo_url.path
        logger.info(f'fetch_pack_v1: git://{host}:{port}{path}')

        # Connect and send handshake
        stream = await trio.open_tcp_stream(host, port)
        handshake = f'git-upload-pack {path}\0host={host}\0'
        await stream.send_all(create_pkt_line(handshake))

        # Read and discard refs (already fetched in fetch_refs)
        content = bytearray()
        while True:
            chunk = await stream.receive_some(4096)
            if not chunk:
                break
            content.extend(chunk)
            if content.endswith(b'0000'):
                break

        # Send want/have/done
        await stream.send_all(payload.build())

        # Receive packfile
        async def stream_iterator():
            while True:
                chunk = await stream.receive_some(65536)
                if not chunk:
                    break
                yield chunk

        pkt_stream = aparse_pkt_line(stream_iterator())
        file_stream = aparse_packfile_stream(pkt_stream)
        data = await agather_bytes(file_stream)

        await stream.aclose()

        atomic_write(output_file, data)

    async def fetch_pack_v2(self, payload: FetchPayload, output_file=None):
        """
        Fetch pack using Git Protocol v2.
        
        Args:
            payload (FetchPayload): The fetch request payload.
            output_file (str): Write into output_file directly.
                If output_file is None, return pack file data.

        Returns:
            bytes | None:
        """
        host = self.arguments.repo_url.host
        port = self.arguments.repo_url.port or 9418
        path = self.arguments.repo_url.path
        logger.info(f'fetch_pack_v2: git://{host}:{port}{path}')

        # Connect and send v2 handshake
        stream = await trio.open_tcp_stream(host, port)
        # v2 handshake includes version=2
        handshake = f'git-upload-pack {path}\0host={host}\0\0version=2\0'
        await stream.send_all(create_pkt_line(handshake))

        # Read server capabilities
        content = bytearray()
        while True:
            chunk = await stream.receive_some(4096)
            if not chunk:
                break
            content.extend(chunk)
            if content.endswith(b'0000'):
                break

        logger.debug(f'Server capabilities: {bytes(content[:200])!r}')

        # Build and send v2 fetch command
        v2_payload = self._build_v2_payload(payload)
        await stream.send_all(v2_payload)

        # Receive packfile
        async def stream_iterator():
            while True:
                chunk = await stream.receive_some(65536)
                if not chunk:
                    break
                yield chunk

        pkt_stream = aparse_pkt_line(stream_iterator())
        file_stream = aparse_packfile_stream(pkt_stream)
        data = await agather_bytes(file_stream)

        await stream.aclose()

        atomic_write(output_file, data)

    def _build_v2_payload(self, payload: FetchPayload):
        """
        Build Protocol v2 format payload.
        
        v2 format:
            command=fetch
            0001  (delimiter)
            want <sha1>
            have <sha1>
            done
            0000
        
        Args:
            payload (FetchPayload): Original v1 payload.
            
        Returns:
            bytes: v2 formatted payload.
        """
        lines = []
        # Command
        lines.append(create_pkt_line('command=fetch\n'))
        # Delimiter
        lines.append(b'0001')
        
        # Extract want/have/done from v1 payload
        v1_content = payload.build()
        
        # Parse v1 payload and convert to v2
        for line in parse_pkt_line(v1_content):
            if not line:
                continue
            
            line_str = line.decode('utf-8', errors='ignore').strip()
            
            if line_str.startswith('want '):
                # Remove capabilities from want line
                sha1 = line_str.split()[1]
                lines.append(create_pkt_line(f'want {sha1}\n'))
            elif line_str.startswith('have '):
                lines.append(create_pkt_line(line_str + '\n'))
            elif line_str.startswith('deepen '):
                lines.append(create_pkt_line(line_str + '\n'))
            elif line_str == 'done':
                lines.append(create_pkt_line('done\n'))
        
        # End with flush-pkt
        lines.append(b'0000')
        
        return b''.join(lines)
