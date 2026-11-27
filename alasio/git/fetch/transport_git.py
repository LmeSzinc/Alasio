import trio

from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_write
from alasio.git.fetch.client_git import DEFAULT_GIT_PORT, AsyncGitClient
from alasio.git.fetch.pkt import (
    FetchPayload, agather_bytes, aparse_packfile_stream, aparse_pkt_line, create_pkt_line, receive_chunks
)
from alasio.git.fetch.transport import BaseTransport
from alasio.logger import logger


class GitTransport(BaseTransport):
    """
    Transport implementation for Git protocol using TCP.

    The protocol itself is the one of AsyncGitClient, which owns the retries:
    this class only names the server of the arguments and logs the transfer.
    """

    @cached_property
    def client(self):
        """
        Returns:
            AsyncGitClient: The retrying client of the server
        """
        repo_url = self.arguments.repo_url
        return AsyncGitClient(
            host=repo_url.host,
            port=repo_url.port or DEFAULT_GIT_PORT,
            path=repo_url.path,
        )

    async def fetch_refs(self):
        """
        Returns:
            dict[bytes, bytes]:
                key: sha1
                value: ref like refs/heads/bug_fix, refs/heads/v2021.10.24, refs/pull/1007/head
        """
        logger.info(f'fetch_refs: {self.client.url}')
        return await self.client.refs()

    async def fetch_pack_v1(self, payload: FetchPayload, output_file=None):
        """
        Args:
            payload (FetchPayload):
            output_file (str): Write into output_file directly
                If output_file is None, return pack file data

        Returns:
            bytes | None:
        """
        logger.info(f'fetch_pack_v1: {self.client.url}')
        return await self.client.fetch_pack(payload, output_file=output_file)

    async def fetch_pack_v2(self, payload: FetchPayload, output_file=None):
        """
        Fetch pack using Git Protocol v2.

        The client does not parse the pack section of protocol v2 yet, so this
        exchange is not part of AsyncGitClient and has neither its checksum nor
        its retries: see test_fetch_pack_v2, which is skipped for the same
        reason.

        Args:
            payload (FetchPayload): The fetch request payload.
            output_file (str): Write into output_file directly.
                If output_file is None, return pack file data.

        Returns:
            bytes | None:
        """
        host = self.arguments.repo_url.host
        port = self.arguments.repo_url.port or DEFAULT_GIT_PORT
        path = self.arguments.repo_url.path
        logger.info(f'fetch_pack_v2: git://{host}:{port}{path}')

        async with await trio.open_tcp_stream(host, port) as stream:
            # Send v2 handshake (includes version=2)
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
            pkt_stream = aparse_pkt_line(receive_chunks(stream))
            file_stream = aparse_packfile_stream(pkt_stream)
            data = await agather_bytes(file_stream)

        if output_file is not None:
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

        # Directly iterate over payload (it's a deque of pkt-lines)
        for pkt_line in payload:
            if pkt_line == b'0000':  # Skip delimiters from v1
                continue

            # Decode pkt-line: skip first 4 bytes (length header)
            if len(pkt_line) > 4:
                content = pkt_line[4:].decode('utf-8', errors='ignore').strip()

                if content.startswith('want '):
                    # Remove capabilities from want line
                    parts = content.split()
                    if len(parts) >= 2:
                        sha1 = parts[1]
                        lines.append(create_pkt_line(f'want {sha1}\n'))
                elif content.startswith('have '):
                    lines.append(create_pkt_line(content + '\n'))
                elif content.startswith('deepen '):
                    lines.append(create_pkt_line(content + '\n'))
                elif content == 'done':
                    lines.append(create_pkt_line('done\n'))

        # End with flush-pkt
        lines.append(b'0000')

        return b''.join(lines)
