"""
The links of a PEP 503 index page.

Ported from `pip/_internal/models/link.py` (pip, MIT): the `Link` class, the
`LinkHash` fragment reader and the url helpers are the source implementation.
`HTMLLinkParser` and `parse_links` are ported from
`pip/_internal/index/collector.py` (pip, MIT).

The parts of the source the fetcher does not use are not copied: the metadata
file of PEP 658, the upload time, the egg fragment of a legacy index and the
vcs/file links. `_ensure_quoted_url` is simplified: the path is unquoted and
quoted back with quote(), instead of the reserved character walk of the source
(a PEP 503 page of a mirror carries http/https links, a local file url never
reaches the fetcher).

Usage:
    from alasio.deploy.pip_download._link import HTMLLinkParser, parse_links

    links = parse_links(page_content, 'https://pypi.org/simple/httpx/', 'text/html')
    links[0].filename        # 'httpx-0.28.1-py3-none-any.whl'
    links[0].hash            # the sha256 of the file, or None
"""

import functools
import json
import os
import posixpath
import re
import urllib.parse
from html.parser import HTMLParser

__all__ = ['HTMLLinkParser', 'Link', 'LinkHash', 'parse_links']

# The extension of a wheel, the only format the fetcher accepts
WHEEL_EXTENSION = '.whl'

# Order matters: the earlier hashes have a precedence over the later ones for
# the hash the fetcher picks
_SUPPORTED_HASHES = ('sha512', 'sha384', 'sha256', 'sha224', 'sha1', 'md5')


def _to_path_component(name):
    """
    Reduce a name to a single path component, '' when it has none.

    Ported from pip/_internal/models/link.py: a name with a directory part, a
    drive letter or a separator is reduced to its last component, and '.',
    '..' and '' are not components.

    Args:
        name (str): Name to reduce, e.g. the last part of a url path

    Returns:
        str: The path component, '' when the name has none
    """
    name = os.path.basename(name)
    if name in ('', os.curdir, os.pardir):
        return ''
    return name


def _absolute_link_url(base_url, url):
    """
    Resolve a link url against the url of the page.

    Ported from pip/_internal/models/link.py: the absolute urls are taken as
    they are, which is the common case of a real mirror page.

    Args:
        base_url (str): Url of the page, or its `<base href>`
        url (str): The href of the link

    Returns:
        str: The absolute url
    """
    if url.startswith(('https://', 'http://')):
        return url
    return urllib.parse.urljoin(base_url, url)


def _ensure_quoted_url(url):
    """
    Quote the path of an url without double quoting the escapes it carries.

    Args:
        url (str): Url to quote

    Returns:
        str: The quoted url
    """
    result = urllib.parse.urlsplit(url)
    if not result.netloc:
        # a local path, not a link of an index page
        return url
    # unquote first, so a character is never quoted twice; '%' stays safe so a
    # stray escape is not turned into '%25'
    path = urllib.parse.quote(urllib.parse.unquote(result.path), safe="/%:@&=+$,;~()!*'")
    return urllib.parse.urlunsplit(result._replace(path=path))


class LinkHash:
    """
    A hash the fragment of a link carries.

    Ported from pip/_internal/models/link.py: the fragment of an index link
    is `#sha256=<hex>`, and a url may carry several hashes.

    Attributes:
        name (str): Name of the hash, e.g. 'sha256'
        value (str): The digest
    """

    _hash_url_fragment_re = re.compile(
        r'[#&]({choices})=([^&]*)'.format(
            choices='|'.join(re.escape(hash_name) for hash_name in _SUPPORTED_HASHES)))

    def __init__(self, name, value):
        """
        Args:
            name (str): Name of the hash, one of _SUPPORTED_HASHES
            value (str): The digest
        """
        self.name = name
        self.value = value

    def __str__(self):
        return f'{self.name}={self.value}'

    def as_dict(self):
        """
        Returns:
            dict[str, str]: The hash as {name: value}, the shape the link
                stores its hashes in
        """
        return {self.name: self.value}

    @classmethod
    def find_hash_url_fragment(cls, url):
        """
        The first supported hash of a url fragment.

        Ported from pip/_internal/models/link.py: the regex keeps the value as
        it is, an invalid digest is not the business of this class.

        Args:
            url (str): Url to read

        Returns:
            LinkHash | None: The hash, None when the url carries none of the
                supported hashes
        """
        match = cls._hash_url_fragment_re.search(url)
        if match is None:
            return None
        return cls(match.group(1), match.group(2))


@functools.total_ordering
class Link:
    """
    One link of a package index page.

    Ported from pip/_internal/models/link.py.

    Attributes:
        url (str): Absolute url of the link, the fragment included
        comes_from (str): Url of the page the link was found on
        requires_python (str): Value of `data-requires-python`, None when the
            link carries none
        yanked_reason (str): Value of `data-yanked`, None when the file is not
            yanked
    """

    __slots__ = [
        '_parsed_url', '_url', '_path', '_hashes',
        'comes_from', 'requires_python', 'yanked_reason',
    ]

    def __init__(self, url, comes_from=None, requires_python=None, yanked_reason=None,
                 hashes=None):
        """
        Args:
            url (str): Url of the link, the href of the anchor
            comes_from (str, optional): Url of the page the link was found on
            requires_python (str, optional): Value of `data-requires-python`
            yanked_reason (str, optional): Value of `data-yanked`
            hashes (dict[str, str], optional): Hashes of the file whose source
                is not the fragment of the url
        """
        self._parsed_url = urllib.parse.urlsplit(url)
        self._url = url
        # the path of the url is hot: unquote it ahead of time
        self._path = urllib.parse.unquote(self._parsed_url.path)

        link_hash = LinkHash.find_hash_url_fragment(url)
        hashes_from_link = {} if link_hash is None else link_hash.as_dict()
        if hashes is None:
            self._hashes = hashes_from_link
        else:
            self._hashes = {**hashes, **hashes_from_link}

        self.comes_from = comes_from
        self.requires_python = requires_python if requires_python else None
        self.yanked_reason = yanked_reason

    @classmethod
    def from_json(cls, file_data, page_url):
        """
        Convert one `files` entry of a PEP 691 page into a Link.

        Ported from pip/_internal/models/link.py.

        Args:
            file_data (dict): One entry of the `files` list
            page_url (str): Url of the page

        Returns:
            Link | None: The link, None when the entry carries no url
        """
        file_url = file_data.get('url')
        if file_url is None:
            return None

        url = _ensure_quoted_url(_absolute_link_url(page_url, file_url))
        pyrequire = file_data.get('requires-python')
        yanked_reason = file_data.get('yanked')
        hashes = file_data.get('hashes', {})

        # the yanked reason of the source is an empty string for a yanked file
        # whose page gives no reason, and None for a file that is not yanked
        if yanked_reason and not isinstance(yanked_reason, str):
            yanked_reason = ''
        elif not yanked_reason:
            yanked_reason = None

        return cls(
            url,
            comes_from=page_url,
            requires_python=pyrequire,
            yanked_reason=yanked_reason,
            hashes=hashes,
        )

    @classmethod
    def from_element(cls, anchor_attribs, page_url, base_url):
        """
        Convert the attributes of an anchor of a PEP 503 page into a Link.

        Ported from pip/_internal/models/link.py.

        Args:
            anchor_attribs (dict[str, str | None]): Attributes of the anchor
            page_url (str): Url of the page
            base_url (str): The `<base href>` of the page, or its url

        Returns:
            Link | None: The link, None when the anchor carries no href
        """
        href = anchor_attribs.get('href')
        if not href:
            return None

        url = _ensure_quoted_url(_absolute_link_url(base_url, href))
        pyrequire = anchor_attribs.get('data-requires-python')
        yanked_reason = anchor_attribs.get('data-yanked')

        return cls(
            url,
            comes_from=page_url,
            requires_python=pyrequire,
            yanked_reason=yanked_reason,
        )

    def __str__(self):
        if self.requires_python:
            rp = f' (requires-python:{self.requires_python})'
        else:
            rp = ''
        if self.comes_from:
            return f'{self.url} (from {self.comes_from}){rp}'
        return f'{self.url}{rp}'

    def __repr__(self):
        return f'<Link {self}>'

    def __hash__(self):
        return hash(self.url)

    def __eq__(self, other):
        if not isinstance(other, Link):
            return NotImplemented
        return self.url == other.url

    def __lt__(self, other):
        if not isinstance(other, Link):
            return NotImplemented
        return self.url < other.url

    @property
    def url(self):
        """
        Returns:
            str: The url of the link, the fragment included
        """
        return self._url

    @property
    def filename(self):
        """
        The filename of the linked file.

        Ported from pip/_internal/models/link.py: the last component of the
        path, or the host of an url whose path has none. The answer is a
        single path component, so the file of a link can never be written
        outside the destination folder.

        Returns:
            str: Filename, e.g. 'httpx-0.28.1-py3-none-any.whl'
        """
        name = _to_path_component(posixpath.basename(self.path.rstrip('/')))
        if name:
            return name

        # no component in the path: fall back to the host, the auth dropped
        netloc = self._parsed_url.netloc
        host = netloc.rpartition('@')[2]
        return _to_path_component(host)

    @property
    def scheme(self):
        """
        Returns:
            str: Scheme of the url, e.g. 'https'
        """
        return self._parsed_url.scheme

    @property
    def netloc(self):
        """
        Returns:
            str: Host of the url, the auth information included
        """
        return self._parsed_url.netloc

    @property
    def path(self):
        """
        Returns:
            str: Unquoted path of the url
        """
        return self._path

    def splitext(self):
        """
        Returns:
            tuple[str, str]: Stem and extension of the filename
        """
        return posixpath.splitext(posixpath.basename(self.path.rstrip('/')))

    @property
    def ext(self):
        """
        Returns:
            str: Extension of the linked file, e.g. '.whl'
        """
        return self.splitext()[1]

    @property
    def url_without_fragment(self):
        """
        Returns:
            str: The url without its fragment, the url of the request
        """
        scheme, netloc, path, query, _ = self._parsed_url
        return urllib.parse.urlunsplit((scheme, netloc, path, query, ''))

    @property
    def hashes(self):
        """
        Returns:
            dict[str, str]: Hashes the link carries, {name: digest}
        """
        return self._hashes

    @property
    def hash(self):
        """
        The strongest hash the link carries.

        Ported from pip/_internal/models/link.py.

        Returns:
            str | None: The digest, None when the link carries no hash
        """
        return next(iter(self._hashes.values()), None)

    @property
    def hash_name(self):
        """
        Returns:
            str | None: Name of the strongest hash the link carries, e.g.
                'sha256'
        """
        return next(iter(self._hashes), None)

    @property
    def show_url(self):
        """
        Returns:
            str: Filename part of the url, for the logs
        """
        return posixpath.basename(self._url.split('#', 1)[0].split('?', 1)[0])

    @property
    def is_wheel(self):
        """
        Returns:
            bool: True for a wheel file
        """
        return self.ext == WHEEL_EXTENSION

    @property
    def is_yanked(self):
        """
        Returns:
            bool: True when the file is yanked
        """
        return self.yanked_reason is not None


class HTMLLinkParser(HTMLParser):
    """
    An HTML parser that keeps the first `<base href>` and every anchor.

    Ported from pip/_internal/index/collector.py.
    """

    def __init__(self, url):
        """
        Args:
            url (str): Url of the page, for the error messages
        """
        super().__init__(convert_charrefs=True)

        self.url = url
        self.base_url = None
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        """
        Record a `<base href>` or an anchor of the page.

        Args:
            tag (str): Name of the tag
            attrs (list[tuple[str, str | None]]): Attributes of the tag
        """
        if tag == 'base' and self.base_url is None:
            href = self.get_href(attrs)
            if href is not None:
                self.base_url = href
        elif tag == 'a':
            self.anchors.append(dict(attrs))

    def get_href(self, attrs):
        """
        The href of a tag.

        Ported from pip/_internal/index/collector.py.

        Args:
            attrs (list[tuple[str, str | None]]): Attributes of the tag

        Returns:
            str | None: The href, None when the tag carries none
        """
        for name, value in attrs:
            if name == 'href':
                return value
        return None


def parse_links(page, page_url, content_type=''):
    """
    Parse the links of a simple index page.

    Ported from pip/_internal/index/collector.py: a PEP 691 JSON page is read
    by its `files` list, an HTML page by its anchors, the urls resolved
    against the `<base href>` of the page when it carries one.

    Args:
        page (bytes | str): Content of the page
        page_url (str): Url of the page
        content_type (str): Content type the mirror answered with, the JSON
            page is selected by it. Defaults to '', an HTML page

    Returns:
        list[Link]: The links of the page, in the order of the page

    Raises:
        json.JSONDecodeError: If the page is a JSON page that does not parse
    """
    if content_type.lower().startswith('application/vnd.pypi.simple.v1+json'):
        if isinstance(page, bytes):
            page = page.decode('utf-8')
        data = json.loads(page)
        links = []
        for file_data in data.get('files', []):
            link = Link.from_json(file_data, page_url)
            if link is not None:
                links.append(link)
        return links

    if isinstance(page, bytes):
        page = page.decode('utf-8', 'replace')
    parser = HTMLLinkParser(page_url)
    parser.feed(page)

    base_url = parser.base_url or page_url
    links = []
    for anchor in parser.anchors:
        link = Link.from_element(anchor, page_url=page_url, base_url=base_url)
        if link is not None:
            links.append(link)
    return links
