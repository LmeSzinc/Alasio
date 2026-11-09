"""
The COS object store: the verbs of a bucket prefix over HttpClient.

COS speaks http, and the requests this store sends are simple enough that
the official SDK is not worth its dependency floor: cos-python-sdk-v5 1.9.44
declares certifi<=2021.10.8 and requests<=2.27.1, and installing it would roll
the certificate store of the whole process back by years. The signature is the
only subtle part, and it is compared byte for byte with the SDK (see sign.py).

What this store does NOT do, on purpose: multipart upload, resumable transfer,
presigned urls, lifecycle / CORS / ACL, CDN purge, versioning, copy and move.
None of them is on the path of "the generator writes a manifest, the uploader
mirrors it, the cleaner removes what the next generation no longer serves", and
each one is larger than the verbs together.

The integrity of an upload rests on Content-MD5: COS compares the header with
the bytes it received before it writes the object, and answers 400 BadDigest
without storing anything when they differ. That protects the transfer, not the
object: it says the bytes arrived intact, never that the key still holds them
when someone reads it later.

The state of the stored object is read without downloading it: PUT, HEAD and GET
answer x-cos-hash-crc64ecma, the CRC64 of the content the server computed when
it stored the object. The uploader compares the value of the PUT with the value
of a HEAD (see ChannelUpload._verify), so the object a reader sees is proven to
be the one that was written, at the cost of one request and zero bytes. A
listing does not carry the header (measured against the bucket), so the
verification of a write is the one place that reads one object at a time.
"""

import base64
import calendar
import os
import re
import time
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit

import trio
from yarl import URL

from alasio.deploy.httpclient.client_async import AsyncHttpClient
from alasio.deploy_dev.store import (
    BucketNotFound, Capabilities, ObjectInfo, ObjectListing, ObjectNotFound, ObjectStore, StoreError, close_local,
    open_local, read_chunks
)
from alasio.deploy_dev.store.cos.sign import CosSigner
from alasio.logger import logger
from alasio.logger.utils import stringify_event

# the Content-Type of an object whose caller gives none: a pack, an index or a
# manifest is served to a program, never rendered by a browser
DEFAULT_CONTENT_TYPE = 'application/octet-stream'

# the largest object a single PUT can write. A larger object would need a
# multipart upload, whose ETag is not the MD5 of the content: the mirror would
# stop recognizing its own objects and resend them on every run
MAX_PUT_SIZE = 5 << 30

# the keys of one listing page, the maximum COS returns per request
PAGE_SIZE = 1000

# the timeouts of the requests. The connect budget stays short (a peer that
# cannot be reached is dropped quickly), the read/write budget of a PUT is
# longer than the 30 s of the shared client: a 100 MB file over a slow uplink
# takes minutes, and a transfer that is slow is not a stalled one
CONNECT_TIMEOUT = 5.0
PUT_TIMEOUT = 120.0
# the read budget of a download: a file is downloaded at the speed it was
# written, so a transfer of a large object takes the same order of time
READ_TIMEOUT = 120.0

# attempts of a request and the wait between them: 0.5 s, then doubled up to a
# cap of 4 s, 8 attempts in total (0.5 + 1 + 2 + 4 + 4 + 4 + 4 = about 20 s of
# waiting). A transport error of a few seconds (a dns lookup that fails while
# the resolver of the machine is busy, a peer that drops a connection) is worth
# waiting for; a refused policy is a status error and is never retried
ATTEMPTS = 8
BACKOFF = 0.5
BACKOFF_MAX = 4.0

# the header of the CRC64 of an object, answered by PUT, HEAD and GET: the
# digest of the content the server computed when it stored the object. A
# listing does not carry it (measured against the bucket), which is why a check
# of a written object costs one HEAD
CRC64_HEADER = 'x-cos-hash-crc64ecma'

# the shape of the LastModified of a listing: an ISO 8601 timestamp in UTC,
# with optional fractional seconds, e.g. 2026-11-06T19:39:59.000Z
ISO8601 = re.compile(
    r'^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?'
    r'(Z|[+-]\d{2}:?\d{2})?$'
)


class CosError(StoreError):
    """
    Raised when COS refuses a request or cannot be reached.

    The attributes are the status and the error code of the server answer, not
    parsed out of a message: a caller that needs to tell a missing bucket from a
    denied key reads them, and the message stays a sentence for a log.

    Attributes:
        status (int): Http status of the answer, None when no answer arrived
        code (str): Code of the XML error body, e.g. 'SignatureDoesNotMatch',
            None when the answer carried no error document
    """

    def __init__(self, message, status=None, code=None):
        """
        Args:
            message (str): Description of the failure, without a credential
            status (int): Http status of the answer. Defaults to None
            code (str): Error code of the answer. Defaults to None
        """
        super().__init__(message)
        self.status = status
        self.code = code


def normalize_etag(etag):
    """
    Normalize an ETag to the form a listing of this store reports.

    COS quotes the ETag and writes the hex of an MD5 in lowercase; a store that
    answers uppercase or unquoted is normalized the same way, so the core flow
    compares the two without knowing which one it got.

    Args:
        etag (str): ETag of a header or of a listing, None when missing

    Returns:
        str: The ETag without quotes and lowercased, None when there was none
    """
    if not etag:
        return None
    etag = etag.strip()
    if len(etag) >= 2 and etag.startswith('"') and etag.endswith('"'):
        etag = etag[1:-1]
    return etag.lower()


def parse_http_date(value):
    """
    Parse a Date or Last-Modified header to unix seconds.

    Args:
        value (str): Value of the header, None when missing

    Returns:
        float: Unix time of the header, None when the value is missing or not a
            date
    """
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        # an http date is gmt, parsedate_to_datetime leaves an offset-less
        # value naive on some versions
        return calendar.timegm(parsed.timetuple())
    return parsed.timestamp()


def parse_iso8601(value):
    """
    Parse the LastModified of a listing to unix seconds.

    The fractional seconds are dropped: the timestamp is compared with the
    clock of the server at whole seconds, so the precision of the fraction is
    noise. Only the shape COS writes is accepted, an unknown one is a broken
    answer, not a value to guess.

    Args:
        value (str): Timestamp, e.g. '2026-11-06T19:39:59.000Z'

    Returns:
        float: Unix time of the timestamp

    Raises:
        ValueError: If the value is not an ISO 8601 timestamp of this shape
    """
    match = ISO8601.match((value or '').strip())
    if not match:
        raise ValueError(f'Not an ISO 8601 timestamp: "{value}"')
    year, month, day, hour, minute, second = (int(group) for group in match.groups()[:6])
    zone = match.group(7)
    parsed = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    if zone and zone != 'Z':
        sign = -1 if zone[0] == '-' else 1
        digits = zone[1:].replace(':', '')
        offset = sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
        parsed = parsed.replace(tzinfo=timezone(timedelta(seconds=offset)))
    return parsed.timestamp()


def strip_namespaces(root):
    """
    Drop the xml namespace of every element of a parsed document.

    COS answers a listing without a namespace and an S3 compatible endpoint
    answers one with a namespace; the parser prefixes the tag of every element
    with '{namespace}' in the second case, and this makes the two shapes read
    the same way.

    Args:
        root (ElementTree.Element): Root of the parsed document, modified in
            place
    """
    for element in root.iter():
        tag = element.tag
        if isinstance(tag, str) and '}' in tag:
            element.tag = tag.rpartition('}')[2]


def validate_relative(relative):
    """
    Check that a key is a plain workspace-relative path.

    Args:
        relative (str): Key to check

    Raises:
        ValueError: If the key is empty, absolute, or has an empty '.' or '..'
            segment
    """
    if not relative:
        raise ValueError('Object key is empty')
    if relative.startswith('/'):
        raise ValueError(f'Object key must be relative: "{relative}"')
    for part in relative.split('/'):
        if part in ('', '.', '..'):
            raise ValueError(f'Object key has an empty, "." or ".." segment: "{relative}"')


def normalize_prefix(prefix):
    """
    Normalize the prefix a store is bound to.

    The prefix always ends with '/': a listing of 'aLas_Repo' must not include
    the objects of 'aLas_Repo2'. An empty prefix means the root of the bucket.

    Args:
        prefix (str): Prefix of the store, with or without the trailing '/'

    Returns:
        str: The prefix, ending with '/' when it is not empty

    Raises:
        ValueError: If the prefix has an empty '.' or '..' segment
    """
    if not prefix:
        return ''
    prefix = prefix.strip('/')
    if not prefix:
        return ''
    for part in prefix.split('/'):
        if part in ('', '.', '..'):
            raise ValueError(f'Object prefix has an empty, "." or ".." segment: "{prefix}"')
    return prefix + '/'


def parse_endpoint(endpoint, bucket, region):
    """
    Resolve the scheme, the host and the port of the requests of a store.

    The three parts are what URL.build() of yarl takes: the host without the
    port and the port on its own, so the standard port of the scheme is dropped
    from the url and an explicit one survives.

    Args:
        endpoint (str): Endpoint of the bucket, e.g. 'https://host:port'. None
            to derive it from the bucket and the region
        bucket (str): Name of the bucket, e.g. 'alas2026-1254325529'
        region (str): Region of the bucket, e.g. 'ap-shanghai'

    Returns:
        tuple[str, str, int]: (scheme, host, port), e.g. ('https',
            'alas2026-1254325529.cos.ap-shanghai.myqcloud.com', None). The
            port is None when the endpoint names none

    Raises:
        ValueError: If the endpoint is not an http(s) url without a path, or
            its port is not a number
    """
    if not endpoint:
        return 'https', f'{bucket}.cos.{region}.myqcloud.com', None
    parts = urlsplit(endpoint if '//' in endpoint else 'https://' + endpoint)
    if parts.scheme not in ('http', 'https'):
        raise ValueError(f'COS endpoint must be http or https: "{endpoint}"')
    if not parts.hostname:
        raise ValueError(f'COS endpoint has no host: "{endpoint}"')
    if parts.path not in ('', '/'):
        raise ValueError(f'COS endpoint must not carry a path: "{endpoint}"')
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f'COS endpoint has an invalid port: "{endpoint}"') from None
    return parts.scheme, parts.hostname, port


class CosStore(ObjectStore):
    """
    The objects of one bucket prefix, read and written through COS.

    The instance is bound to `prefix`; every key it takes is relative to it and
    every key it returns is relative to it, so a caller never builds a bucket
    key and never sees the physical layout. The requests go through
    AsyncHttpClient, so a run of dozens of transfers is a set of trio tasks, not
    a pool of threads; the client is created on the first request and shared by
    every task.

    Attributes:
        bucket (str): Name of the bucket
        region (str): Region of the bucket
        prefix (str): Key prefix the store is bound to, '' for the bucket root
        scheme (str): 'https', or 'http' for a local endpoint of a test
        host (str): Host of the requests, without the port
        port (int): Port of the requests, None when the url names none
    """

    CAPABILITIES = Capabilities(
        etag_md5=True,
        head_checksum=True,
        dirs=False,
    )

    MAX_PUT_SIZE = MAX_PUT_SIZE
    PAGE_SIZE = PAGE_SIZE
    ATTEMPTS = ATTEMPTS
    BACKOFF = BACKOFF
    BACKOFF_MAX = BACKOFF_MAX

    def __init__(
        self, bucket, region, secret_id, secret_key, token=None, endpoint=None,
        prefix='', client=None, clock=time.time,
    ):
        """
        Args:
            bucket (str): Name of the bucket, e.g. 'alas2026-1254325529'
            region (str): Region of the bucket, e.g. 'ap-shanghai'
            secret_id (str): SecretId of the credential
            secret_key (str): SecretKey of the credential, never rendered
            token (str): Session token of a temporary credential. Defaults to
                None
            endpoint (str): Endpoint of the bucket, e.g. a local fake server of
                the tests. Defaults to None: https and the myqcloud host of the
                bucket and the region
            prefix (str): Key prefix the store is bound to, e.g.
                'pack/Author_Repo_master'. Defaults to ''
            client (AsyncHttpClient | httpx2.AsyncClient): Injected client, the
                store sends its requests with it and never closes it. Defaults
                to None: the store creates its client on the first request
            clock (Callable[[], float]): Source of the request time of the
                signature, frozen in tests. Defaults to time.time

        Raises:
            ValueError: If a required argument is empty or an endpoint or a
                prefix is not usable
        """
        if not bucket:
            raise ValueError('COS bucket is empty')
        if not region:
            raise ValueError('COS region is empty')
        self.bucket = bucket
        self.region = region
        self.prefix = normalize_prefix(prefix)
        self.scheme, self.host, self.port = parse_endpoint(endpoint, bucket, region)
        # the url every request is built from: the scheme, the host and the port
        # are assembled by yarl, so an ipv6 host, a port and the default port of
        # the scheme need no string arithmetic here, and the path of a request
        # replaces the '/' of this base
        self._base = URL.build(scheme=self.scheme, host=self.host, port=self.port, path='/')
        self._signer = CosSigner(secret_id, secret_key, token=token, clock=clock)
        self._client = client if isinstance(client, AsyncHttpClient) else AsyncHttpClient(client)

    @property
    def authority(self):
        """
        The value of the Host header: the host with the port when it is not the
        standard one of the scheme, e.g. 'alasio-1250000000.cos.ap-shanghai.myqcloud.com'
        or '127.0.0.1:53124'.

        Returns:
            str: Authority of the base url, the same bytes the signature signs
        """
        return self._base.raw_authority

    # ------------------------------------------------------------------
    # the verbs of ObjectStore
    # ------------------------------------------------------------------

    async def list(self):
        """
        Every object under the prefix of this store.

        A listing of COS is a page of at most PAGE_SIZE keys; the pages are
        followed with the continuation token of the previous one. A key that
        ends with '/' is the placeholder of a folder a console created, not an
        object of the workspace, and is skipped.

        Returns:
            ObjectListing: The objects and the Date of the first page

        Raises:
            CosError: If a page fails or does not parse
        """
        objects = {}
        server_date = None
        token = None
        while True:
            params = {'list-type': '2', 'max-keys': str(self.PAGE_SIZE)}
            if self.prefix:
                params['prefix'] = self.prefix
            if token:
                params['continuation-token'] = token
            response = await self._call('GET', None, params=params)
            entries, truncated, token, date = self._parse_listing(response)
            if server_date is None:
                server_date = date
            for key, info in entries:
                relative = self._relative(key)
                if relative is not None:
                    objects[relative] = info
            if not truncated:
                break
        return ObjectListing(
            objects=objects,
            server_date=server_date if server_date is not None else time.time(),
        )

    async def get(self, relative):
        """
        Read one object.

        Args:
            relative (str): Relative key of the object

        Returns:
            bytes: Content of the object

        Raises:
            ObjectNotFound: If the object does not exist
            CosError: If the read fails
        """
        return (await self._call('GET', relative)).content

    async def put(self, relative, file, size, md5, content_type=None, cache_control=None):
        """
        Write one object with a single PUT.

        Each attempt opens the file again and reads it through a new chunk
        iterable. A body is consumed once: httpx reads an async iterable, and a
        generator that was already drained yields nothing for the second
        attempt, so a retry of the same body would send zero bytes while it
        still declares the original Content-Length (measured on httpx2 2.12.0,
        for the sync file object and for the async iterable alike). That is also
        why the retry is here and not in the http client: the client retries the
        request it was given, and the body of the second attempt would be empty.

        Args:
            relative (str): Relative key of the object
            file (str): Path of the local file to send
            size (int): Size of the file in bytes
            md5 (str): Lowercase hex MD5 of the content
            content_type (str): Content-Type of the object, DEFAULT_CONTENT_TYPE
                when None. Defaults to None
            cache_control (str): Cache-Control of the object, unset when None.
                Defaults to None

        Returns:
            ObjectInfo: What the server reports about the written object

        Raises:
            CosError: If the write fails: a BadDigest answer means the file
                changed while it was being uploaded
        """
        if size < 0:
            raise ValueError(f'Size of "{relative}" is negative: {size}')
        if size > self.MAX_PUT_SIZE:
            raise CosError(
                f'Object "{relative}" is {size} bytes, larger than the single PUT '
                f'limit of {self.MAX_PUT_SIZE}; a multipart upload is not supported'
            )
        try:
            content_md5 = base64.b64encode(bytes.fromhex(md5)).decode('ascii')
        except ValueError:
            raise ValueError(f'MD5 of "{relative}" is not a hex string: "{md5}"') from None
        import httpx2

        timeout = httpx2.Timeout(
            connect=CONNECT_TIMEOUT,
            read=PUT_TIMEOUT,
            write=PUT_TIMEOUT,
            pool=CONNECT_TIMEOUT,
        )
        headers = {
            'Content-Length': str(size),
            'Content-MD5': content_md5,
            'Content-Type': content_type or DEFAULT_CONTENT_TYPE,
        }
        if cache_control:
            headers['Cache-Control'] = cache_control
        for attempt in range(1, self.ATTEMPTS + 1):
            # signed per attempt: a retry after a long wait must not carry a
            # signature that has aged out
            url, signed = self._prepare('PUT', relative, None, headers)
            try:
                # the open, the stat and the reads run in worker threads: the
                # event loop must not wait for the disk while other transfers
                # of the run are in flight
                f = await open_local(file)
                try:
                    # the size of the open file, not a stat of the path: one
                    # lookup instead of two, and the value cannot change
                    # between the check and the read (a file replaced in that
                    # window would be declared with one length and read with
                    # another, which the http stack reports as a broken
                    # transfer and the loop retries eight times)
                    actual = await trio.to_thread.run_sync(lambda: os.fstat(f.fileno()).st_size)
                    if actual != size:
                        raise ValueError(
                            f'Size of "{relative}" is declared {size} bytes, '
                            f'the file has {actual}')
                    response = await self._client.request(
                        'PUT', url, headers=signed, content=read_chunks(f),
                        timeout=timeout, attempts=1)
                finally:
                    await close_local(f)
                return await self._put_result(response, relative, size)
            except httpx2.HTTPStatusError as e:
                raise self._cos_error(relative, e.response) from None
            except httpx2.TransportError as e:
                await self._retry_or_raise('PUT', relative, attempt, e)

    async def delete(self, relative):
        """
        Delete one object, a missing key is a success.

        Args:
            relative (str): Relative key of the object

        Raises:
            CosError: If the delete fails for another reason
        """
        try:
            await self._call('DELETE', relative)
        except ObjectNotFound:
            # the object of a cleanup run may already be gone: a second run, or
            # an object removed by hand; the state the caller wants is reached
            return

    async def aclose(self):
        """
        Close the client of this store when the store created it.
        """
        await self._client.aclose()

    # ------------------------------------------------------------------
    # a single object, for a caller that wants one request
    # ------------------------------------------------------------------

    async def head(self, relative):
        """
        Read the metadata of one object with a single HEAD.

        The verification of a write reads it: the CRC64 of the stored object is
        compared with the one the PUT answered, so the flow proves the object
        a reader sees is the one that was written without downloading it (see
        ChannelUpload._verify). The state of a whole workspace is one listing
        (see stat()), this is the one object request.

        Args:
            relative (str): Relative key of the object

        Returns:
            ObjectInfo: Size, ETag, Last-Modified and the CRC64 of the object

        Raises:
            ObjectNotFound: If the object does not exist
            CosError: If the request fails
        """
        response = await self._call('HEAD', relative)
        return self._info_from_headers(response, relative)

    # ------------------------------------------------------------------
    # requests
    # ------------------------------------------------------------------

    def _object_key(self, relative):
        """
        The full key of an object of this store.

        Args:
            relative (str): Relative key of the object

        Returns:
            str: Prefix and key, e.g. 'pack/Author_Repo/latest.pack'

        Raises:
            ValueError: If the key is not a plain relative path
        """
        validate_relative(relative)
        return self.prefix + relative

    def _encoded_path(self, key):
        """
        The path of the request url of a key: every segment is escaped.

        A segment is escaped down to the unreserved set of RFC 3986, which is
        stricter than what yarl keeps in a path: yarl leaves '+', '&', '=' and
        the other sub-delimiters of a path unescaped, and COS decodes a literal
        '+' of a path as a space, so the object would be written under another
        key and the signature would not match (measured against the bucket: a
        PUT of 'a+b.txt' built by yarl is refused with SignatureDoesNotMatch,
        every other sub-delimiter is accepted). The escaped path is handed to
        yarl with encoded=True, so nothing is escaped twice.

        Args:
            key (str): Full key of the object

        Returns:
            str: Path of the url, e.g. '/pack/%E4%B8%AD%E6%96%87/a%2Bb.bin'
        """
        return '/' + '/'.join(quote(part, safe='') for part in key.split('/'))

    def _url(self, key, params):
        """
        Build the url of a request.

        yarl assembles the scheme, the host, the port, the path and the query:
        no string is joined here, and the standard port of the scheme is dropped
        on its own. The path is handed over already escaped, see _encoded_path.

        Args:
            key (str): Full key of the object, None for the bucket itself
            params (dict): Query parameters

        Returns:
            URL: Url of the request
        """
        url = self._base
        if key is not None:
            url = url.with_path(self._encoded_path(key), encoded=True)
        if params:
            # sorted, because the signature covers the parameters in name order
            url = url.with_query(sorted(params.items()))
        return url

    def _prepare(self, method, relative, params=None, headers=None):
        """
        Build the url and the headers of a signed request.

        The two halves are built from the same key but are not the same string:
        the url carries the escaped path, the signature covers the decoded key.
        COS decodes the path of the request before it rebuilds the canonical
        string, so a signed escaped path is refused as soon as a key needs an
        escape at all ('a b', '中文', 'a+b'); an ascii key hides the difference,
        which is why it is stated here and covered by the tests.

        Args:
            method (str): Http method
            relative (str): Relative key, None for a request of the bucket itself
            params (dict): Query parameters. Defaults to None
            headers (dict): Request headers. Defaults to None

        Returns:
            tuple[str, dict]: The url and the headers, Authorization included
        """
        if relative is None:
            key = None
            sign_path = '/'
        else:
            key = self._object_key(relative)
            sign_path = '/' + key
        params = params or {}
        headers = dict(headers or {})
        headers['Host'] = self.authority
        if self._signer.token:
            # a x-cos-* header: it is signed like every other one, so a
            # temporary credential never sends an unsigned token
            headers['X-Cos-Security-Token'] = self._signer.token
        headers['Authorization'] = self._signer.authorization(method, sign_path, params, headers)
        return str(self._url(key, params)), headers

    async def _call(self, method, relative, params=None, headers=None, **kwargs):
        """
        Send one request and map its failure to the errors of the store.

        The retry of a transport error is here, not in the http client, for two
        reasons: the wait between the attempts is the exponential one of
        retry_wait() (0.5, 1, 2, then 4 s, 8 attempts), while the client waits a
        fixed RETRY_BACKOFF; and every attempt is signed again here, so a
        request that waited out a long failure never carries an aged signature.

        Args:
            method (str): Http method
            relative (str): Relative key, None for a request of the bucket itself
            params (dict): Query parameters. Defaults to None
            headers (dict): Request headers. Defaults to None
            **kwargs: Any other argument of AsyncHttpClient.request() (content,
                timeout, ...)

        Returns:
            httpx2.Response: The response of a successful request

        Raises:
            ObjectNotFound: If the object does not exist
            CosError: If the server refuses the request or cannot be reached
        """
        import httpx2

        for attempt in range(1, self.ATTEMPTS + 1):
            url, signed = self._prepare(method, relative, params, headers)
            try:
                # attempts=1: the client sends the request as it is given, the
                # loop of this method owns the retry and the wait (see above)
                return await self._client.request(
                    method, url, headers=signed, attempts=1, **kwargs)
            except httpx2.HTTPStatusError as e:
                raise self._cos_error(relative, e.response) from None
            except httpx2.TransportError as e:
                await self._retry_or_raise(method, relative, attempt, e)

    @staticmethod
    def retry_wait(attempt):
        """
        Seconds to wait after a failed attempt.

        Args:
            attempt (int): One based index of the attempt that just failed

        Returns:
            float: 0.5, 1, 2, then 4 for every later attempt
        """
        return min(BACKOFF * (2 ** (attempt - 1)), BACKOFF_MAX)

    async def _retry_or_raise(self, method, relative, attempt, error):
        """
        Wait for another attempt, or raise when there is none left.

        Args:
            method (str): Http method of the failed attempt
            relative (str): Relative key of the failed attempt
            attempt (int): One based index of the attempt that just failed
            error (Exception): The transport error of the attempt

        Raises:
            CosError: When the last attempt failed, or the wait was cancelled
        """
        if attempt >= self.ATTEMPTS:
            raise CosError(
                f'{method} of "{self._describe(relative)}" failed after {attempt} attempts: '
                f'{stringify_event(error)}'
            ) from None
        wait = self.retry_wait(attempt)
        logger.warning(
            f'{method} of "{self._describe(relative)}" failed (attempt {attempt}/{self.ATTEMPTS}): '
            f'{stringify_event(error)}, retrying in {wait:g}s')
        await trio.sleep(wait)

    # ------------------------------------------------------------------
    # answers
    # ------------------------------------------------------------------

    def _describe(self, relative):
        """
        Name an object of this store in a message.

        Args:
            relative (str): Relative key, None for the bucket itself

        Returns:
            str: The key with its prefix, or the bucket name
        """
        if relative is None:
            return f'{self.bucket}/{self.prefix}'
        return self.prefix + relative

    def _cos_error(self, relative, response):
        """
        Build the error of a refused request from its status and error body.

        The error document of COS is an xml of <Error><Code>...</Code>
        <Message>...</Message></Error>; the code is the part a caller can act on
        (a missing key, a missing bucket, a denied policy, a signature of a clock
        that is off). The body of a signature error also echoes the canonical
        string, which holds no secret but is noise in a log: only Code and
        Message are read.

        A missing bucket and a missing key are both a 404, and the code of the
        error document is the only thing that tells them apart. The two are
        reported differently because they ask for different actions: a bucket
        that does not exist is a remote that has to be configured again, while a
        key that does not exist is a file that has to be written. A 404 without
        a code is the empty body of a HEAD, and is read as a missing key: a HEAD
        cannot answer which of the two it was.

        Args:
            relative (str): Relative key of the request
            response (httpx2.Response): The refused response

        Returns:
            StoreError: BucketNotFound for a missing bucket, ObjectNotFound for
                a missing key, CosError otherwise
        """
        status = response.status_code
        code, message = self._parse_error(response)
        name = self._describe(relative)
        if code == 'NoSuchBucket':
            return BucketNotFound(
                f'bucket "{self.bucket}" in region "{self.region}" does not exist '
                f'({message or "NoSuchBucket"}): check the Bucket and Region of the remote'
            )
        if code == 'NoSuchKey' or (status == 404 and code is None):
            return ObjectNotFound(f'Object "{name}" does not exist')
        detail = f'{status}'
        if code:
            detail += f' {code}'
        if message:
            detail += f': {message}'
        if code == 'BadDigest':
            detail += ' (the local file changed while it was being uploaded)'
        elif code in ('SignatureDoesNotMatch', 'InvalidAccessKeyId', 'AccessDenied'):
            detail += (
                ' (check the sub account policy of the bucket prefix and the clock'
                ' of this machine, a signature is valid for a limited time)')
        return CosError(f'Request of "{name}" failed: {detail}', status=status, code=code)

    def _parse_error(self, response):
        """
        Read the Code and the Message of an error document.

        A streamed request that fails was never read: its body is not available
        here, and the status is the part of the answer that names the failure.

        Args:
            response (httpx2.Response): The refused response

        Returns:
            tuple[str, str]: (code, message), (None, None) when the response
                carries no error document, e.g. the empty body of a HEAD or the
                closed stream of a readback
        """
        import httpx2

        try:
            content = response.content
        except httpx2.ResponseNotRead:
            # the answer of a streamed request: the body was never read, and
            # the stream is closed by the time the error is mapped
            return None, None
        if not content:
            return None, None
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            # not an error document: a proxy page or a broken answer, only the
            # status is trusted
            return None, None
        strip_namespaces(root)
        code = root.findtext('Code')
        message = root.findtext('Message')
        if message:
            message = message.strip()[:200]
        return code, message

    def _parse_listing(self, response):
        """
        Read one page of a ListObjectsV2 answer.

        Args:
            response (httpx2.Response): The answer of the page

        Returns:
            tuple[list, bool, str, float]: ([(key, ObjectInfo)], IsTruncated,
                NextContinuationToken, [Date]) of the page

        Raises:
            CosError: If the document does not parse or is truncated without a
                token to continue with
        """
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as e:
            raise CosError(f'LIST returned malformed xml: {e}') from None
        strip_namespaces(root)

        entries = []
        for element in root.findall('Contents'):
            key = element.findtext('Key')
            if not key:
                continue
            entries.append((key, self._info_from_element(element, key)))

        truncated = (root.findtext('IsTruncated') or '').strip().lower() == 'true'
        token = root.findtext('NextContinuationToken') or None
        if truncated and not token:
            raise CosError('LIST is truncated but carries no NextContinuationToken')
        return entries, truncated, token, parse_http_date(response.headers.get('Date'))

    def _info_from_element(self, element, key):
        """
        Read the ObjectInfo of one Contents element of a listing.

        Args:
            element (ElementTree.Element): A Contents element
            key (str): Key of the element, for the message of a broken value

        Returns:
            ObjectInfo: Size, ETag and LastModified of the object

        Raises:
            CosError: If a value of the element is missing or not a timestamp
        """
        try:
            size = int(element.findtext('Size'))
        except (TypeError, ValueError):
            raise CosError(f'LIST reports no usable size for "{key}"') from None
        try:
            last_modified = parse_iso8601(element.findtext('LastModified'))
        except ValueError as e:
            raise CosError(f'LIST reports an unusable LastModified for "{key}": {e}') from None
        return ObjectInfo(
            size=size,
            etag=normalize_etag(element.findtext('ETag')) or '',
            last_modified=last_modified,
        )

    def _info_from_headers(self, response, relative):
        """
        Read the ObjectInfo of a HEAD or of a PUT answer.

        Args:
            response (httpx2.Response): The answer
            relative (str): Relative key, for the message of a broken answer

        Returns:
            ObjectInfo: Size, ETag and LastModified of the object

        Raises:
            CosError: If the answer carries no size or no ETag
        """
        size = response.headers.get('Content-Length')
        etag = normalize_etag(response.headers.get('ETag'))
        if size is None or not etag:
            raise CosError(
                f'Answer for "{self._describe(relative)}" carries no '
                f'Content-Length or ETag: {dict(response.headers)}'
            )
        last_modified = parse_http_date(response.headers.get('Last-Modified'))
        if last_modified is None:
            last_modified = parse_http_date(response.headers.get('Date'))
        return ObjectInfo(
            size=int(size),
            etag=etag,
            last_modified=last_modified if last_modified is not None else time.time(),
            checksum=response.headers.get(CRC64_HEADER, ''),
        )

    async def _put_result(self, response, relative, size):
        """
        Read the ObjectInfo of a successful PUT.

        The ETag of a single PUT is the MD5 of the content; a server that does
        not answer it (the S3 protocol allows it) costs one HEAD, which is
        cheaper than a listing of the whole prefix.

        The last_modified of this answer is the Date of the response, not the
        moment the object was written: the two can differ by a second, because
        the write lands before the answer is stamped (measured against the
        bucket). A caller that needs the age of the object reads it from a
        listing or from head(), the two answers that report the write; the
        core flow does exactly that and never uses the value of a put().

        Args:
            response (httpx2.Response): The answer of the PUT
            relative (str): Relative key of the object
            size (int): Size of the file that was sent

        Returns:
            ObjectInfo: Size, ETag, LastModified and the CRC64 of the object

        Raises:
            CosError: If the answer carries no ETag and the HEAD fails
        """
        etag = normalize_etag(response.headers.get('ETag'))
        if not etag:
            return await self.head(relative)
        last_modified = parse_http_date(response.headers.get('Date'))
        return ObjectInfo(
            size=size,
            etag=etag,
            last_modified=last_modified if last_modified is not None else time.time(),
            checksum=response.headers.get(CRC64_HEADER, ''),
        )

    def _relative(self, key):
        """
        Turn a key of a listing into the relative key of this store.

        Args:
            key (str): Key reported by the listing

        Returns:
            str: The key without the prefix, None for a key outside the prefix
                or for the placeholder of a folder
        """
        if not key.startswith(self.prefix):
            return None
        relative = key[len(self.prefix):]
        if not relative or relative.endswith('/'):
            # the placeholder of a folder a console created is not an object of
            # a workspace: a mirror serves files
            return None
        return relative
