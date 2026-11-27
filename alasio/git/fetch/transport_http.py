import httpx2

from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.ext.path.atomic import atomic_write
from alasio.git.fetch.pkt import FetchPayload, agather_bytes, aparse_packfile_stream, aparse_pkt_line, parse_pkt_line
from alasio.git.fetch.transport import BaseTransport
from alasio.git.file.exception import PackBroken, PktLineBroken
from alasio.logger import logger


class GitHttpClient(AsyncHttpClient):
    """
    The retrying http client of the domain, with the options of the git protocol.

    http2 and the redirects are the options of a smart-http request of git, and
    the proxy is the one of the arguments: an explicit proxy, never the one of
    the environment, so a proxy tool that left its variables behind cannot route
    a fetch. The rest is the design of AsyncHttpClient: an error of
    retry_errors() is retried up to the attempts of the call, an http status is
    the answer of the server and is raised as it is.
    """

    def __init__(self, proxy=None):
        """
        Args:
            proxy (str, optional): Proxy of the arguments, None for a direct
                connection. Defaults to None
        """
        super().__init__()
        self._proxy = proxy

    @classmethod
    def retry_errors(cls):
        """
        The failures of one attempt that another attempt may fix: the transport
        errors of AsyncHttpClient, and an answer that arrived broken, i.e. a
        pack that does not match its own trailing sha1 (see
        aparse_packfile_stream) or a pkt-line stream cut in the middle of a
        line. The chunked encoding of http does not hide either of them from
        the reader of the body.

        Returns:
            tuple[type[BaseException], ...]: The types retried
        """
        return AsyncHttpClient.retry_errors() + (PackBroken, PktLineBroken)

    def create_client(self):
        """
        The client of this instance: the client of the domain, with the options
        above.

        An instance method rather than the classmethod of the domain, because
        the proxy belongs to one fetch. The base creates the client of an
        instance through self, see HttpClientBase._get_aclient().

        Returns:
            httpx2.AsyncClient: The client to send the requests with
        """
        return httpx2.AsyncClient(
            http2=True, follow_redirects=True, trust_env=False, proxy=self._proxy)


class HttpTransport(BaseTransport):
    """
    Transport implementation for HTTP/S protocol using `httpx2`.

    Every request goes through GitHttpClient, so a connection that never opened
    is retried instead of failing the fetch.
    """

    def _client(self):
        """
        The client of one request of this transport.

        A client per request, closed by the request that created it: the
        transport has no lifetime of its own to close one with.

        Returns:
            GitHttpClient: The client to send the request with
        """
        return GitHttpClient(self.arguments.proxy)

    async def fetch_refs(self):
        """
        Returns:
            dict[bytes, bytes]:
                key: sha1
                value: ref like refs/heads/bug_fix, refs/heads/v2021.10.24, refs/pull/1007/head
        """
        repo = self.arguments.repo_url.to_http()
        url = f'{repo}/info/refs?service=git-upload-pack'
        logger.info(f'fetch_refs: {url}')

        headers = self.capabilities.headers(protocol_v2=False)
        client = self._client()
        try:
            response = await client.get(url, headers=headers)
        finally:
            await client.aclose()
        content = response.content

        out = {}
        for line in parse_pkt_line(content):
            # parse line, which might be :
            # # service=git-upload-pack\n
            # d5f854cedd666de9a99ebacbd3fb47971afc4271 HEAD\x00multi_ack thin-pack ...
            # cbba8f024a69c24380efe82b3b0ec3c736d01dfb refs/heads/bug_fix\n
            # 8b63ab79e956b90805dff2167448a72262fe50eb refs/heads/v2021.10.24\n
            # b9f14832259b2b093a03fdfd5c611baceea7c926 refs/pull/1007/head\n
            # b408a075f13681edd44fcbb17d452bdd8aaf67aa refs/tags/v2020.04.08\n
            sha1, sep, ref = line.strip().partition(b' ')
            if not sep or not ref.startswith(b'refs/'):
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
        repo = self.arguments.repo_url.to_http()
        url = f'{repo}/git-upload-pack'
        logger.info(f'fetch_refs: {url}')

        headers = self.capabilities.headers(protocol_v2=False)
        headers.update({
            'Content-Type': 'application/x-git-upload-pack-request',
            'Accept': 'application/x-git-upload-pack-result',
        })
        content = payload.build()

        # the pack is read out of the streamed answer, and a transfer that
        # failed leaves its parsed bytes behind with the attempt
        data = None

        async def consume(response):
            nonlocal data
            pkt_stream = aparse_pkt_line(response.aiter_raw())
            file_stream = aparse_packfile_stream(pkt_stream)
            data = await agather_bytes(file_stream)

        client = self._client()
        try:
            await client.stream(url, consume, method='POST', headers=headers, content=content)
        finally:
            await client.aclose()

        if output_file is not None:
            atomic_write(output_file, data)

        return data

    async def fetch_pack_v2(self, payload: FetchPayload, output_file=None):
        """
        Fetch pack using HTTP Protocol v2.

        Args:
            payload (FetchPayload): The fetch request payload.
            output_file (str): Write into output_file directly.
                If output_file is None, return pack file data.

        Returns:
            bytes | None:
        """
        repo = self.arguments.repo_url.to_http()
        url = f'{repo}/git-upload-pack'
        logger.info(f'fetch_pack_v2: {url}')

        headers = self.capabilities.headers(protocol_v2=True)
        headers.update({
            'Content-Type': 'application/x-git-upload-pack-request',
            'Accept': 'application/x-git-upload-pack-result',
        })

        # Build v2 payload: command=fetch + delimiter + want/have/done
        content = self._build_v2_payload(payload)

        data = None

        async def consume(response):
            nonlocal data
            pkt_stream = aparse_pkt_line(response.aiter_raw())
            file_stream = aparse_packfile_stream(pkt_stream)
            data = await agather_bytes(file_stream)

        client = self._client()
        try:
            await client.stream(url, consume, method='POST', headers=headers, content=content)
        finally:
            await client.aclose()

        if output_file is not None:
            atomic_write(output_file, data)

        return data

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
        from alasio.git.fetch.pkt import create_pkt_line

        lines = []
        # Command
        lines.append(create_pkt_line('command=fetch\n'))
        # Delimiter
        lines.append(b'0001')

        # Directly iterate over payload (it's a deque of pkt-lines)
        # We need to extract the actual content from each pkt-line
        for pkt_line in payload:
            if pkt_line == b'0000':  # Skip delimiters from v1
                continue

            # Decode pkt-line: skip first 4 bytes (length header)
            if len(pkt_line) > 4:
                content = pkt_line[4:].decode('utf-8', errors='ignore').strip()

                if content.startswith('want '):
                    # Remove capabilities from want line (everything after first space after SHA)
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
