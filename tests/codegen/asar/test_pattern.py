"""
Tests of the glob matching used by include / exclude / unpack / unpack_dir.
"""
import pytest

from alasio.codegen.asar.pattern import match_any, match_dir, match_path, translate


class TestTranslate:
    @pytest.mark.parametrize('pattern, expected', [
        ('a', '^a$'),
        ('a.txt', '^a\\.txt$'),
        ('*', '^[^/]*$'),
        ('*.js', '^[^/]*\\.js$'),
        ('?', '^[^/]$'),
        ('a?c', '^a[^/]c$'),
        ('**', '^.*$'),
        ('a/**', '^a/.*$'),
        ('**/a', '^(?:.*/)?a$'),
        ('a/**/b', '^a/(?:.*/)?b$'),
        ('**/*.js', '^(?:.*/)?[^/]*\\.js$'),
        ('a+b', '^a\\+b$'),
        ('a.b', '^a\\.b$'),
        ('[a-z]', '^\\[a\\-z\\]$'),
        ('', '^$'),
    ])
    def test_translate(self, pattern, expected):
        """Glob patterns are translated to anchored regular expressions."""
        assert translate(pattern) == expected


class TestMatchPath:
    @pytest.mark.parametrize('path, pattern, expected', [
        # A star does not cross a separator
        ('a.js', '*.js', True),
        ('dir/a.js', '*.js', False),
        ('dir/a.js', 'dir/*.js', True),
        ('dir/sub/a.js', 'dir/*.js', False),
        ('dir/sub/a.js', 'dir/**/*.js', True),
        ('dir/a.js', 'dir/**/*.js', True),
        ('a.js', '**/*.js', True),
        ('dir/sub/a.js', '**/*.js', True),
        ('dir/sub/a.js', '**', True),
        ('dir', 'dir/**', False),
        ('dir/a', 'dir/**', True),
        # A question mark matches exactly one character, and not a separator
        ('ab', 'a?', True),
        ('a', 'a?', False),
        ('a/b', 'a?b', False),
        # Anchored on both ends
        ('a.js', 'a', False),
        ('a.js.bak', '*.js', False),
        # Case sensitive on every platform
        ('A.JS', '*.js', False),
        ('a.js', '*.JS', False),
        # Literal characters are escaped
        ('a.js', 'a.js', True),
        ('aXjs', 'a.js', False),
        ('a+b', 'a+b', True),
        ('aab', 'a+b', False),
        # Character classes are literal, not supported
        ('[a-z]', '[a-z]', True),
        ('a', '[a-z]', False),
    ])
    def test_match_path(self, path, pattern, expected):
        """Patterns cover the whole path and follow the documented rules."""
        assert match_path(path, pattern) is expected

    @pytest.mark.parametrize('path, pattern, expected', [
        ('dir/a.js', '*.js', False),
        ('dir/a.js', '**/*.js', True),
        ('dir/dir2/a.js', 'dir2', False),
    ])
    def test_match_path_without_base(self, path, pattern, expected):
        """A path is not matched on its basename unless asked for."""
        assert match_path(path, pattern, match_base=False) is expected

    @pytest.mark.parametrize('path, pattern, expected', [
        ('dir/a.js', '*.js', True),
        ('dir/a.js', 'a.js', True),
        ('dir/a.js', '*.txt', False),
        ('dir/a.js', 'dir/*.js', True),
        ('a.js', '*.js', True),
    ])
    def test_match_path_with_base(self, path, pattern, expected):
        """A pattern without a separator is also matched on the basename."""
        assert match_path(path, pattern, match_base=True) is expected

    def test_match_path_empty_path(self):
        """An empty path only matches an empty pattern."""
        assert match_path('', '') is True
        assert match_path('', '*') is True
        assert match_path('', 'a') is False


class TestMatchAny:
    @pytest.mark.parametrize('path, patterns, expected', [
        # A pattern without a separator does not match a nested path
        ('dir/a.js', ['*.txt', '*.js'], False),
        ('dir/a.js', ['*.txt', '**/*.js'], True),
        ('dir/a.js', ['*.txt'], False),
        ('dir/a.js', [], False),
        ('dir/a.js', ['**/*.js'], True),
    ])
    def test_match_any(self, path, patterns, expected):
        """A path matches when at least one pattern matches."""
        assert match_any(path, patterns) is expected

    def test_match_any_base(self):
        """The basename matching is passed to every pattern."""
        assert match_any('dir/a.js', ['*.js'], match_base=True) is True
        assert match_any('dir/a.js', ['*.js'], match_base=False) is False


class TestMatchDir:
    @pytest.mark.parametrize('path, pattern, expected', [
        # A literal prefix is accepted for backward compatibility
        ('dir2', 'dir2', True),
        ('dir2/sub', 'dir2', True),
        ('dir22', 'dir2', True),
        ('dir1', 'dir2', False),
        # A glob pattern is matched on the whole path
        ('dir2', 'dir*', True),
        ('dir2', 'dir*/*', False),
        ('dir2/sub', 'dir*', False),
        ('dir2/sub', 'dir2/*', True),
        ('dir2', 'dir2/*', False),
        ('dir2/sub', '**/sub', True),
    ])
    def test_match_dir(self, path, pattern, expected):
        """A directory matches a prefix or a glob pattern, like the asar CLI."""
        assert match_dir(path, pattern) is expected
