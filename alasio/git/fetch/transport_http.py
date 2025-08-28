from io import BytesIO

import httpx
import trio

from alasio.ext.path.atomic import atomic_write, file_write_stream
from alasio.git.fetch.argument import Arguments
from alasio.git.fetch.pkt import FetchPayload, agather_bytes, aparse_packfile_stream, aparse_pkt_line, parse_pkt_line
from alasio.git.fetch.transport import BaseTransport
from alasio.logger import logger


class HttpTransport(BaseTransport):
    """Transport implementation for HTTP/S protocol using `httpx`."""

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
        async with httpx.AsyncClient(
                http2=True, follow_redirects=True, trust_env=False, proxy=self.arguments.proxy
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
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

        async with httpx.AsyncClient(
                http2=True, follow_redirects=True, trust_env=False, proxy=self.arguments.proxy
        ) as client:
            async with client.stream('POST', url, content=content, headers=headers) as response:
                response.raise_for_status()
                pkt_stream = aparse_pkt_line(response.aiter_raw())
                file_stream = aparse_packfile_stream(pkt_stream)
                data = await agather_bytes(file_stream)

        atomic_write(output_file, data)

