"""
The client of the git:// protocol: one connection, one exchange, retried.

A git:// transfer is the twin of an http transfer. The client connects, sends
the handshake of the repository it wants, reads the advertisement, sends the
request of the transfer (want / have / done) and reads the pack. `git daemon`
closes the connection after the pack, so one connection serves exactly one
exchange, and a retry always starts over with a fresh connection.

The retry policy is the one of the http clients (see
alasio.deploy.httpclient.client_base): the same attempts, the same backoff and
the same log of a failed attempt, with the failures of this protocol declared
in retry_errors(). The pack is checked by its own trailing sha1 while it
streams (see aparse_packfile_stream), so a transfer cut short is a failed
attempt that is retried, never a truncated pack handed to the caller.
"""
import trio

from alasio.deploy.httpclient.client_base import RetryPolicyBase
from alasio.ext.path.atomic import atomic_write
from alasio.git.fetch.pkt import (
    agather_bytes, aparse_packfile_stream, aparse_pkt_line, create_pkt_line, parse_pkt_line, receive_chunks
)
from alasio.git.file.exception import PackBroken, PktLineBroken
from alasio.logger import logger

# Port of a git:// server when the url does not give one
DEFAULT_GIT_PORT = 9418
# Bytes of the ref advertisement to read at once
ADVERTISEMENT_SIZE = 4096


def parse_refs(content):
    """
    Parse the ref advertisement of a server into a mapping of sha1 to ref.

    Args:
        content (bytes): The advertisement, up to and including its flush-pkt

    Returns:
        dict[bytes, bytes]:
            key: sha1
            value: ref like refs/heads/bug_fix, refs/pull/1007/head
    """
    out = {}
    for line in parse_pkt_line(content):
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
            logger.warning(f'parse_refs: Unexpected sha1 length "{sha1}"')
            continue
        # set
        out[sha1] = ref

    return out


class AsyncGitClient(RetryPolicyBase):
    """
    Retrying client of the git:// protocol, over trio.

    The twin of AsyncHttpClient for the git transport: it owns the retry
    policy of the project (attempts, backoff, the log of a failed attempt) and
    declares the failures of this protocol in retry_errors(), so the transport
    above it only speaks protocol and never repeats a transfer by itself.

    Unlike an http client, which is a pool of connections reused by every
    request, a git:// connection serves one exchange and is closed by the
    server: the client holds the address of the repository, not a connection,
    and every attempt opens its own.
    """

    def __init__(self, host, port, path):
        """
        Args:
            host (str): Host of the server
            port (int): Port of the server
            path (str): Path of the repository on the server, e.g. /server.git
        """
        self.host = host
        self.port = port
        self.path = path

    @property
    def url(self):
        """
        Returns:
            str: Url of the repository, e.g. git://127.0.0.1:51235/server.git
        """
        return f'git://{self.host}:{self.port}{self.path}'

    @classmethod
    def retry_errors(cls):
        """
        The failures of one attempt that another attempt may fix.

        A connection failure (refused, reset, dns), a pack that did not arrive
        complete or does not match its own checksum, and a pkt-line stream cut
        in the middle of a line. The last two cannot be seen on the socket: a
        server that resets the connection after Windows discarded the bytes
        still unread ends the stream cleanly, so the shape of the answer is the
        only witness of the loss.

        Returns:
            tuple[type[BaseException], ...]: The types retried
        """
        return (OSError, trio.BrokenResourceError, PackBroken, PktLineBroken)

    async def _retry(self, attempt, attempts):
        """
        Run one attempt of an exchange, and run it again while it fails with an
        error this client retries.

        Args:
            attempt (Callable[[], Awaitable]): One attempt, called with no
                argument; it must leave nothing of a failed attempt behind, a
                retry starts from a fresh connection
            attempts (int): Attempts of the exchange

        Returns:
            Any: What the attempt that succeeded returned

        Raises:
            Exception: The failure of the last attempt, when no attempt is
                left or the failure is not an error this client retries
        """
        retry_errors = self.retry_errors()
        for index in range(attempts):
            if index:
                # a reset of the server is often a one-off blip of the network:
                # wait a moment, then run the same exchange from scratch
                await trio.sleep(self.RETRY_BACKOFF)
            try:
                return await attempt()
            except Exception as e:
                # every failed attempt is logged; only an error of
                # retry_errors() is retried while an attempt is left
                self._log_attempt_failure(self.url, index, attempts, e)
                if not isinstance(e, retry_errors) or index + 1 >= attempts:
                    raise

    def _handshake(self):
        """
        Build the service request that opens a connection.

        Returns:
            bytes: The pkt-line of the handshake
        """
        return create_pkt_line(f'git-upload-pack {self.path}\0host={self.host}\0')

    async def _read_advertisement(self, stream):
        """
        Read the ref advertisement of the server, up to its flush-pkt.

        Args:
            stream (trio.abc.Stream): The connection

        Returns:
            bytes: The advertisement, flush-pkt included
        """
        content = bytearray()
        while True:
            chunk = await stream.receive_some(ADVERTISEMENT_SIZE)
            if not chunk:
                break
            content.extend(chunk)
            if content.endswith(b'0000'):
                break

        return bytes(content)

    async def refs(self, attempts=RetryPolicyBase.REQUEST_ATTEMPTS):
        """
        Get the ref advertisement of the repository.

        Args:
            attempts (int): Attempts of the transfer. Defaults to
                REQUEST_ATTEMPTS, a single-shot transfer passes 1

        Returns:
            dict[bytes, bytes]:
                key: sha1
                value: ref like refs/heads/bug_fix, refs/tags/v2021.10.24
        """

        async def attempt():
            async with await trio.open_tcp_stream(self.host, self.port) as stream:
                await stream.send_all(self._handshake())
                content = await self._read_advertisement(stream)

            return parse_refs(content)

        return await self._retry(attempt, attempts)

    async def fetch_pack(self, payload, output_file=None, attempts=RetryPolicyBase.REQUEST_ATTEMPTS):
        """
        Fetch the pack of a request, retried while the attempt fails.

        Every attempt opens a fresh connection and runs the whole exchange
        again: a pack cannot be resumed, the server sends it from the first
        byte.

        Args:
            payload (FetchPayload): The request, want / have / done
            output_file (str): Write the pack into output_file directly. When
                None the pack is returned and not written anywhere
            attempts (int): Attempts of the transfer. Defaults to
                REQUEST_ATTEMPTS, a single-shot transfer passes 1

        Returns:
            bytes: The pack

        Raises:
            PackBroken: If every attempt ended with a pack that is not complete
                or does not match its own checksum
            OSError: If every attempt failed to reach the server
        """

        async def attempt():
            async with await trio.open_tcp_stream(self.host, self.port) as stream:
                await stream.send_all(self._handshake())
                # The advertisement is already known by the caller of the
                # transfer, it is discarded here
                await self._read_advertisement(stream)

                await stream.send_all(payload.build())

                pkt_stream = aparse_pkt_line(receive_chunks(stream))
                file_stream = aparse_packfile_stream(pkt_stream)
                # the checksum of the pack is checked as it streams, a
                # truncated pack raises out of this gather
                return await agather_bytes(file_stream)

        data = await self._retry(attempt, attempts)
        if output_file is not None:
            atomic_write(output_file, data)

        return data
