"""
The headers a mirror serves a key with, and the keys that are never cached.

The two decisions are pure functions of the key, so they are tested without a
store: a mirror that served the manifest with a year of cache would keep a
client on a version the server no longer has, and one that served a pack as
text/html would be read by a browser instead of a program.
"""

import pytest

from alasio.deploy_dev.pack_server.publish.common import (
    BARRIER_KEY, DEFAULT_CONTENT_TYPE, IMMUTABLE_CACHE, MANIFEST_KEY, NO_CACHE, cache_control, content_type, is_no_cache
)

pytestmark = [
    pytest.mark.parallel,
]


class TestContentType:
    """The MIME type of a key, from its suffix."""

    @pytest.mark.parametrize('relative, expected', [
        ('manifest.json', 'application/json; charset=utf-8'),
        ('index.html', 'text/html; charset=utf-8'),
        ('style.css', 'text/css; charset=utf-8'),
        ('app.js', 'text/javascript; charset=utf-8'),
        ('notes.txt', 'text/plain; charset=utf-8'),
        ('packrepo/aaaaaaaa/full.pack', DEFAULT_CONTENT_TYPE),
        ('wheel/x/1.0/x.whl', DEFAULT_CONTENT_TYPE),
        # a dot in a folder is not a suffix, and a suffix is case-insensitive
        ('dir.with.dots/file', DEFAULT_CONTENT_TYPE),
        ('PAGE.HTML', 'text/html; charset=utf-8'),
        # only the last suffix counts
        ('archive.tar.gz', DEFAULT_CONTENT_TYPE),
    ])
    def test_the_suffix_decides(self, relative, expected):
        """Every branch of the table, and the fallback for a name without one."""
        assert content_type(relative) == expected


class TestCacheControl:
    """Which keys a client or a CDN must not cache."""

    @pytest.mark.parametrize('relative, immutable', [
        # the two files that name a generation
        (MANIFEST_KEY, False),
        (BARRIER_KEY, False),
        # a page of the wheel index names the current versions as well
        ('wheel/index.html', False),
        ('packdep/index.json', False),
        # the content of a generation never changes
        ('packrepo/aaaaaaaa/full.pack', True),
        ('wheel/x/1.0/x.whl', True),
        ('notes.txt', True),
        ('style.css', True),
        # the dotted folder of the cache and a name without a suffix
        ('.wheel_cache/httpx/0.28.1/httpx-0.28.1.whl', True),
        ('README', True),
    ])
    def test_the_generation_files_are_never_cached(self, relative, immutable):
        """A key that names a version is revalidated, the content is immutable."""
        assert is_no_cache(relative) is not immutable
        assert cache_control(relative) == (IMMUTABLE_CACHE if immutable else NO_CACHE)
