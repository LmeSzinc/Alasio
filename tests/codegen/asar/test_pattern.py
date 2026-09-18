"""
Tests of the glob matching used by include / exclude / unpack / unpack_dir.
"""
import pytest

from alasio.codegen.asar.pattern import GlobDirPattern, GlobPattern, translate


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


class TestGlobPattern:
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
    def test_match(self, path, pattern, expected):
        """Patterns cover the whole path and follow the documented rules."""
        assert GlobPattern(pattern).match(path) is expected

    @pytest.mark.parametrize('path, pattern, expected', [
        ('dir/a.js', '*.js', True),
        ('dir/a.js', 'a.js', True),
        ('dir/a.js', '*.txt', False),
        ('dir/a.js', 'dir/*.js', True),
        ('a.js', '*.js', True),
    ])
    def test_match_base(self, path, pattern, expected):
        """A pattern without a separator is also matched on the basename."""
        assert GlobPattern(pattern, match_base=True).match(path) is expected

    def test_match_base_is_skipped_with_a_separator(self):
        """A pattern with a separator always covers the whole path."""
        assert GlobPattern('*/a.js', match_base=True).match('dir/a.js') is True
        assert GlobPattern('*/a.js', match_base=True).match('x/dir/a.js') is False

    def test_match_many_patterns(self):
        """A path is selected when any pattern of the set selects it."""
        pattern = GlobPattern(['*.py', 'dir/*.js'])
        assert pattern.patterns == ('*.py', 'dir/*.js')
        assert pattern.match('a.py') is True
        assert pattern.match('dir/a.js') is True
        assert pattern.match('dir/a.txt') is False

    def test_match_single_pattern_is_not_iterated(self):
        """One pattern stays one pattern, its characters are not patterns."""
        pattern = GlobPattern('*.js')
        assert pattern.patterns == ('*.js',)
        assert pattern.match('a.js') is True
        assert pattern.match('dir/a.js') is False

    @pytest.mark.parametrize('path, patterns, expected', [
        # A pattern reaches its own subtree, and the way to it
        ('dist', ['dist/**'], True),
        ('dist/main', ['dist/**'], True),
        ('dist', ['dist/main/*.js'], True),
        ('dist/main', ['dist/main/*.js'], True),
        ('dist/preload', ['dist/main/*.js'], False),
        ('node_modules', ['dist/**'], False),
        ('node_modules/pkg', ['dist/**'], False),
        # A pattern that starts with a metacharacter matches anywhere
        ('node_modules', ['**/*.js'], True),
        ('node_modules', ['*.js', 'dist/**'], True),
        # The literal part of a pattern is inside its first segment, so a match
        # can be anywhere under the root
        ('node_modules', ['dist*/a'], True),
        # A pattern without a metacharacter selects one path only
        ('node_modules', ['package.json'], False),
        ('dist', ['package.json'], False),
        ('package', ['package.json'], False),
        ('dist', ['package.json', 'dist/**'], True),
        ('dir', ['dir/sub/c.txt'], True),
        ('dir/sub', ['dir/sub/c.txt'], True),
        ('dir/other', ['dir/sub/c.txt'], False),
        # An empty pattern selects the root only
        ('dist', [''], False),
    ])
    def test_may_match_below(self, path, patterns, expected):
        """Only the directories a pattern can reach are worth walking."""
        assert GlobPattern(patterns).may_match_below(path) is expected

    def test_may_match_below_root(self):
        """The root holds every entry, the walk always starts there."""
        assert GlobPattern(['node_modules', 'package.json']).may_match_below('') is True

    def test_match_empty_path(self):
        """An empty path only matches an empty pattern."""
        assert GlobPattern('').match('') is True
        assert GlobPattern('*').match('') is True
        assert GlobPattern('a').match('') is False

    def test_repr(self):
        """A pattern shows itself, useful when debugging a pattern list."""
        assert repr(GlobPattern('*.js')) == "GlobPattern('*.js')"
        assert repr(GlobPattern(['*.js', '*.txt'])) == "GlobPattern('*.js', '*.txt')"


class TestGlobDirPattern:
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
    def test_match(self, path, pattern, expected):
        """A directory matches a prefix or a glob pattern, like the asar CLI."""
        assert GlobDirPattern(pattern).match(path) is expected

    def test_match_many_patterns(self):
        """A directory is selected when any pattern of the set selects it."""
        pattern = GlobDirPattern(['dist', '**/assets'])
        assert pattern.match('dist') is True
        assert pattern.match('dist/sub') is True
        assert pattern.match('a/assets') is True
        assert pattern.match('other') is False

    def test_repr(self):
        """A directory pattern shows itself."""
        assert repr(GlobDirPattern('dist')) == "GlobDirPattern('dist')"
