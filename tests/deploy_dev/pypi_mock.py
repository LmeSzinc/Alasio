"""
An in-memory PyPI simple index, the double of the network for the tests.

The index serves what a metadata reader reads: the PEP 503 page of a
distribution, its files next to the page, the PEP 658 metadata file of a file
that declares one, and the byte ranges of a file. The range answers are what
makes the difference between the hosts a reader meets: PyPI serves a suffix
range, a GitHub release asset refuses it with 501 but serves an explicit one,
and a host without range support answers 200 with the whole file.

Nothing reaches the network and nothing reaches the disk: the requests go
through an httpx2 MockTransport, the files live in the memory of the index.

Usage:
    from tests.deploy_dev.pypi_mock import MockIndex, make_wheel

    index = MockIndex()
    index.add('demo', '1.0', requires=['sniffio'], content=make_wheel('demo', '1.0', ['sniffio']))
    client = index.client
"""

import io
import tarfile
import zipfile
from hashlib import sha256

import httpx2

from alasio.deploy.pip_download._link import parse_links
from alasio.deploy.simple_pip.pip_list import normalize_name

__all__ = [
    'INDEX_URL',
    'METADATA_SUFFIX',
    'IndexEntry',
    'MockIndex',
    'make_sdist',
    'make_wheel',
    'metadata_text',
]

# The base url of the mock index, the value the tests give the manager and the
# requirements file as the source of a pin
INDEX_URL = 'http://index.example/simple'

# The suffix PEP 658 appends to the url of a wheel for its metadata file
METADATA_SUFFIX = '.metadata'


def metadata_text(name, version, requires=()):
    """
    The METADATA of a distribution, the headers the readers read.

    Args:
        name (str): Distribution name as the publisher writes it
        version (str): Version of the distribution
        requires (Iterable[str]): The ``Requires-Dist`` values

    Returns:
        str: Content of a METADATA file
    """
    headers = [
        'Metadata-Version: 2.1',
        f'Name: {name}',
        f'Version: {version}',
    ]
    headers.extend(f'Requires-Dist: {text}' for text in requires)
    return '\n'.join(headers) + '\n\nThe description of the distribution.\n'


def make_wheel(name, version, requires=(), trail=b''):
    """
    A minimal wheel: a zip holding the METADATA of the distribution.

    The members are written in the order the pack tools write them, the
    METADATA before the RECORD, so a caller that passes a RECORD longer than the
    tail of the reader (TAIL_SIZE) puts the METADATA outside that tail: the
    reader has to ask for a window of the archive, which is the case the
    one-request read does not cover.

    Args:
        name (str): Distribution name as the publisher writes it
        version (str): Version of the distribution
        requires (Iterable[str]): The ``Requires-Dist`` values
        trail (bytes): Content of the RECORD written after the METADATA,
            which is what pushes the METADATA away from the tail of the archive.
            Defaults to b'', a wheel whose METADATA is in the tail

    Returns:
        bytes: Content of the .whl file
    """
    dist_info = f'{name.replace("-", "_")}-{version}.dist-info'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'{dist_info}/METADATA', metadata_text(name, version, requires))
        if trail:
            zf.writestr(f'{dist_info}/RECORD', trail)
    return buffer.getvalue()


def make_sdist(name, version, requires=(), metadata_version='2.1', requires_txt=None):
    """
    A minimal source distribution: a tar.gz holding the metadata of a project.

    Args:
        name (str): Distribution name as the publisher writes it
        version (str): Version of the distribution
        requires (Iterable[str]): The ``Requires-Dist`` values of the PKG-INFO
        metadata_version (str): Core metadata version of the PKG-INFO. Defaults
            to '2.1', the first version whose Requires-Dist a reader trusts;
            '1.1' is the legacy metadata that declares no dependency
        requires_txt (str | None): Content of the
            ``<name>.egg-info/requires.txt`` of a setuptools sdist. Defaults to
            None, no egg-info record

    Returns:
        bytes: Content of the .tar.gz file
    """
    headers = [
        f'Metadata-Version: {metadata_version}',
        f'Name: {name}',
        f'Version: {version}',
    ]
    headers.extend(f'Requires-Dist: {text}' for text in requires)
    folder = f'{name}-{version}'
    members = {f'{folder}/PKG-INFO': '\n'.join(headers) + '\n'}
    if requires_txt is not None:
        members[f'{folder}/{name}.egg-info/requires.txt'] = requires_txt
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tf:
        for path, content in members.items():
            data = content.encode('utf-8')
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class IndexEntry:
    """
    One file of the mock index.

    Attributes:
        filename (str): Filename of the file
        content (bytes): Content served for the file
        digest (str): sha256 hex of the content, the hash of the page link
        requires_python (str | None): Value of `data-requires-python`
        yanked (str | None): Value of `data-yanked`
        metadata (str | None): Value of `data-dist-info-metadata`, None when the
            index declares no separate metadata file
        metadata_body (bytes): Content served at `{filename}.metadata`
    """

    __slots__ = (
        'content', 'digest', 'filename', 'metadata', 'metadata_body', 'requires_python', 'yanked',
    )

    def __init__(self, filename, content, requires_python=None, yanked=None,
                 metadata=None, metadata_body=b''):
        self.filename = filename
        self.content = content
        self.digest = sha256(content).hexdigest()
        self.requires_python = requires_python
        self.yanked = yanked
        self.metadata = metadata
        self.metadata_body = metadata_body


class MockIndex:
    """
    An in-memory PyPI simple index, answered by an httpx2 MockTransport handler.

    The page of a distribution is built from the entries registered for it: one
    anchor per file, a relative href, the sha256 of the file in the fragment and
    the PEP 503 attributes the entry declares. The file is served next to its
    page, `{base}/{dist-key}/{filename}`, the PEP 658 metadata of a file at
    `{base}/{dist-key}/{filename}.metadata`, and a byte range of a file from the
    bytes it holds.

    Attributes:
        base (str): Base url of the index
        files (dict[str, dict[str, IndexEntry]]): {dist key: {filename: entry}}
        requests (list[str]): Urls of the requests, in order
        ranges (list[str]): The Range header of every request, in the order of
            requests, '' for a request without one
        range_supported (bool): True to answer a Range request with the bytes it
            asks for, False to answer 200 with the whole file (a host that
            ignores the header)
        suffix_range_supported (bool): True to answer `bytes=-N` with the tail,
            False to answer 501 (the S3 host of a GitHub release asset)
        serve_metadata (bool): True to serve the PEP 658 metadata file of a
            wheel that declares one, False to answer 404 (an index that
            declares the attribute but does not serve the file)
        client (httpx2.AsyncClient): The client the code under test sends with
    """

    def __init__(self, base=INDEX_URL):
        """
        Args:
            base (str): Base url of the index. Defaults to INDEX_URL
        """
        self.base = base
        self.files = {}
        self.requests = []
        # the Range header of every request, in the order of requests
        self.ranges = []
        self.range_supported = True
        self.suffix_range_supported = True
        self.serve_metadata = True
        # True to answer every file request with a 500: the failure of a file
        # is not a property of its version, a reader must stop instead of
        # picking another one
        self.fail_files = False
        self.client = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))

    def add(self, name, version, tag='py3-none-any', filename='', requires=(),
            requires_python=None, yanked=None, metadata=None, content=None, trail=b''):
        """
        Register one file of a distribution.

        Args:
            name (str): Distribution name as the publisher writes it
            version (str): Version of the file
            tag (str): Trailing tags of the wheel filename, e.g. 'py3-none-any'
                or 'cp38-cp38-win_amd64'
            filename (str): Filename of the file. Defaults to '', a wheel
                filename built from the other arguments
            requires (Iterable[str]): The Requires-Dist values of the METADATA
            requires_python (str): Value of `data-requires-python`. Defaults to
                None, the file declares nothing
            yanked (str): Value of `data-yanked`. Defaults to None, not yanked
            metadata (str): Value of `data-dist-info-metadata`: 'true' or
                'sha256', the two forms the readers read. Defaults to None, the
                index serves no separate metadata
            content (bytes): Content of the file. Defaults to None, a fake
                content built from the filename (not a zip archive)
            trail (bytes): Content of the RECORD of the wheel this method
                builds, see make_wheel(). Unused when content is given

        Returns:
            IndexEntry: The registered entry
        """
        if not filename:
            filename = f'{name.replace("-", "_")}-{version}-{tag}.whl'
        if content is None:
            # a real wheel, so a reader of the METADATA reads a zip archive:
            # the attributes of the page are not the only thing a tool reads
            content = make_wheel(name, version, requires, trail)
        body = metadata_text(name, version, requires).encode('utf-8')
        if metadata == 'sha256':
            metadata = f'sha256={sha256(body).hexdigest()}'
        entry = IndexEntry(
            filename, content,
            requires_python=requires_python,
            yanked=yanked,
            metadata=metadata,
            metadata_body=body,
        )
        self.files.setdefault(normalize_name(name), {})[filename] = entry
        return entry

    def links(self, name):
        """
        The links the page of a distribution serves.

        Args:
            name (str): Distribution name as the publisher writes it

        Returns:
            list[Link]: The links of the page

        Raises:
            KeyError: If the index has no such distribution
        """
        body, content_type = self.page(name)
        return parse_links(body, f'{self.base}/{normalize_name(name)}/', content_type)

    def page(self, name):
        """
        The page of a distribution, as the index serves it.

        Args:
            name (str): Distribution name as the publisher writes it

        Returns:
            tuple[bytes, str]: (body, content type)

        Raises:
            KeyError: If the index has no such distribution
        """
        return _page_body(self.files[normalize_name(name)])

    def file_requests(self):
        """
        The requests that transferred a file, the pages excluded.

        Returns:
            list[str]: Urls of the requests, in order
        """
        return [url for url in self.requests if url.endswith('.whl') or METADATA_SUFFIX in url]

    def handler(self, request):
        """
        Answer one request of the client.

        Args:
            request (httpx2.Request): The request

        Returns:
            httpx2.Response: The page or the file, a 404 for an unknown path
        """
        url = str(request.url)
        self.requests.append(url)
        self.ranges.append(request.headers.get('range', ''))
        path = url.split('://', 1)[1].partition('/')[2]
        prefix = self.base.split('://', 1)[1].partition('/')[2].rstrip('/')
        if prefix and path.startswith(f'{prefix}/'):
            path = path[len(prefix) + 1:]
        dist_key, sep, filename = path.partition('/')

        if not sep or not filename:
            dist = self.files.get(dist_key)
            if dist is None:
                return httpx2.Response(404)
            body, content_type = _page_body(dist)
            return httpx2.Response(200, content=body, headers={'content-type': content_type})

        if self.fail_files:
            return httpx2.Response(500)

        if filename.endswith(METADATA_SUFFIX):
            entry = self.files.get(dist_key, {}).get(filename[:-len(METADATA_SUFFIX)])
            if entry is None or not entry.metadata or not self.serve_metadata:
                return httpx2.Response(404)
            body = entry.metadata_body
        else:
            entry = self.files.get(dist_key, {}).get(filename)
            if entry is None:
                return httpx2.Response(404)
            body = entry.content
        if request.method == 'HEAD':
            return httpx2.Response(200, headers={'content-length': str(len(body))})
        return self._range_or_whole(body, request.headers.get('range', ''))

    def _range_or_whole(self, body, value):
        """
        Answer a request that may carry a Range header.

        Args:
            body (bytes): Content of the file
            value (str): Value of the Range header, '' when the request carries
                none

        Returns:
            httpx2.Response: The bytes asked for, the whole file when the host
                is set not to support the range, or 416 when the range does not
                name bytes of the file
        """
        if not value or not self.range_supported:
            return httpx2.Response(200, content=body)
        unit, sep, spec = value.partition('=')
        if not sep or unit.strip().lower() != 'bytes':
            return httpx2.Response(200, content=body)
        total = len(body)
        start, dash, end = spec.partition('-')
        if not dash:
            return httpx2.Response(200, content=body)
        if not start:
            # a suffix range: the last N bytes, the form the S3 host of a
            # GitHub release asset refuses
            if not self.suffix_range_supported:
                return httpx2.Response(501)
            length = int(end or 0)
            first = max(total - length, 0)
            last = total - 1
        else:
            first = int(start)
            last = total - 1 if not end else min(int(end), total - 1)
        if first >= total or first > last:
            return httpx2.Response(416)
        return httpx2.Response(
            206, content=body[first:last + 1],
            headers={'content-range': f'bytes {first}-{last}/{total}'})


def _page_body(dist):
    """
    The PEP 503 page of one distribution of the index.

    Args:
        dist (dict[str, IndexEntry]): {filename: entry} of the distribution

    Returns:
        tuple[bytes, str]: (body, content type)
    """
    anchors = []
    for entry in dist.values():
        attrs = [f'href="{entry.filename}"']
        if entry.requires_python:
            attrs.append(f'data-requires-python="{entry.requires_python}"')
        if entry.yanked is not None:
            attrs.append(f'data-yanked="{entry.yanked}"')
        if entry.metadata:
            attrs.append(f'data-dist-info-metadata="{entry.metadata}"')
        anchors.append(f'<a {" ".join(attrs)}>{entry.filename}</a>')
    body = f'<!DOCTYPE html><html><body>{"".join(anchors)}</body></html>'
    return body.encode('utf-8'), 'text/html'
