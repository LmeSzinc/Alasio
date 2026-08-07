import httpx
from msgspec import Struct

from alasio.deploy.pack.decode_base import PackDecodeError
from alasio.ext.algorithm.vint import decode_vint


class LatestInfo(Struct):
    """
    Latest version and the checksum of its index pack, read from
    latest.pack.
    """
    # latest version, e.g. the commit sha1 string
    version: str
    # sha1 checksum of the index pack of that version, hex string
    checksum: str


class ServerFile:
    """
    HTTP client of the update server, downloads pack files with range
    requests.

    The server layout follows the draft in PackEncodeBase:
    - base_url/latest.pack: latest version in bytes, followed by the
      20 bytes sha1 checksum of the index pack of that version
    - base_url/{version}/full.pack: full pack of a version, the front
      part of it is the index pack of that version

    get_index_pack() downloads the index section with two range
    requests: the header plus the index section length first, then
    the exact range of the index pack.
    """

    # bytes to request first for the header: the pack header plus the
    # index section length vint (at most 8 bytes for a int64 length)
    HEADER_REQUEST_SIZE = 64

    def __init__(self, base_url, client=None):
        """
        Args:
            base_url (str): Base URL of the update server
            client (httpx.Client, optional): Client to reuse, a new
                one is created for every request if not given
        """
        self.base_url = base_url
        self._client = client

    def get_latest_info(self):
        """
        Get the latest version and the checksum of its index pack from
        base_url/latest.pack.

        Returns:
            LatestInfo: The latest version and the index pack checksum

        Raises:
            PackDecodeError: If the response is shorter than the
                20 bytes checksum
            httpx.HTTPStatusError: If the request fails
        """
        response = self._http_get(f'{self.base_url}/latest.pack')
        data = response.content
        if len(data) <= 20:
            raise PackDecodeError(
                f'Failed to read latest.pack: {len(data)} bytes, expected version + 20 bytes checksum'
            )
        # version in bytes + 20 bytes checksum of the index pack
        return LatestInfo(
            version=data[:-20].decode('utf-8', errors='replace'),
            checksum=data[-20:].hex(),
        )

    def get_file_content(self, version, offset, size):
        """
        Get a range of the full pack of a version from
        base_url/{version}/full.pack with an http range request.

        Args:
            version (str): Version to query
            offset (int): Start offset of the range
            size (int): Length of the range

        Returns:
            bytes: File content of the range

        Raises:
            httpx.HTTPStatusError: If the request fails
        """
        url = f'{self.base_url}/{version}/full.pack'
        headers = {'Range': f'bytes={offset}-{offset + size - 1}'}
        response = self._http_get(url, headers)
        # a 200 response means the server ignored the range request,
        # slice the full content then
        if response.status_code == 200:
            return response.content[offset:offset + size]
        return response.content

    def get_index_pack(self, version):
        """
        Get the index pack of a version from
        base_url/{version}/full.pack.

        The index pack is the front part of the full pack: the header
        plus the index section. The section length includes the
        trailing 20 bytes checksum, so the downloaded index pack is
        complete and self-validating with PackDecodeBase. Two range
        requests are made, as planned in PackEncodeBase:
        1. range 0~63, the header and the index section length
        2. range 0 ~ len(header) + len(index section), the index pack

        Args:
            version (str): Version to query

        Returns:
            bytes: Index pack of the version

        Raises:
            PackDecodeError: If the index section length vint is not
                terminated inside the header response
            httpx.HTTPStatusError: If a request fails
        """
        header = self.get_file_content(version, 0, self.HEADER_REQUEST_SIZE)
        try:
            # decode_vint raises on a truncated stream (a high byte at the
            # end of the header response), the vint is always terminated here
            length, read = decode_vint(header[5:])
        except ValueError as e:
            raise PackDecodeError(f'Failed to decode index section length: {e}') from e
        # the index pack is the header plus the index section, including
        # the length vint itself
        return self.get_file_content(version, 0, 5 + read + length)

    def _http_get(self, url, headers=None):
        """
        Get a url with the injected client, or a new one per request.

        Args:
            url (str): URL to get
            headers (dict, optional): Request headers

        Returns:
            httpx.Response: The response

        Raises:
            httpx.HTTPStatusError: If the request fails
        """
        client = self._client
        if client is None:
            with httpx.Client() as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                return response
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response
