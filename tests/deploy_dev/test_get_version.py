import pytest
from alasio.deploy_dev.get_version import get_version_from_source


class TestGetVersionFromSource:
    """Tests for get_version_from_source with Python source code."""

    def test_version_double_underscore(self):
        """__version__ string assignment."""
        source = '__version__ = "1.2.3"\n'
        assert get_version_from_source(source) == '1.2.3'

    def test_version_plain(self):
        """version string assignment without underscores."""
        source = 'version = "0.8.0"\n'
        assert get_version_from_source(source) == '0.8.0'

    def test_version_concatenation(self):
        """String concatenation with + operator."""
        source = '__version__ = "1.0." + "dev"\n'
        assert get_version_from_source(source) == '1.0.dev'

    def test_version_long_concatenation(self):
        """Multiple string concatenations."""
        source = '__version__ = "1." + "2." + "3"\n'
        assert get_version_from_source(source) == '1.2.3'

    def test_priority_over_version(self):
        """__version__ takes priority over version."""
        source = 'version = "0.0.1"\n__version__ = "2.0.0"\n'
        assert get_version_from_source(source) == '2.0.0'

    def test_no_version(self):
        """No version variable defined returns None."""
        source = 'x = 42\n'
        assert get_version_from_source(source) is None

    def test_fstring_without_interpolation(self):
        """f-string with no interpolation is extracted."""
        source = '__version__ = f"3.0.0"\n'
        assert get_version_from_source(source) == '3.0.0'

    def test_fstring_with_interpolation(self):
        """f-string with interpolation is rejected (returns None)."""
        source = 'x = "1.0"\n__version__ = f"{x}.0"\n'
        assert get_version_from_source(source) is None

    def test_non_string_assignment(self):
        """Numeric assignment is rejected."""
        source = '__version__ = 1\n'
        assert get_version_from_source(source) is None

    def test_implicit_concatenation(self):
        """Adjacent string literals (implicit concatenation) are merged."""
        source = '__version__ = "1.0" "rc1"\n'
        assert get_version_from_source(source) == '1.0rc1'

    def test_implicit_concatenation_multiple(self):
        """Multiple adjacent string literals."""
        source = '__version__ = "A" "B" "C"\n'
        assert get_version_from_source(source) == 'ABC'

    def test_mixed_implicit_and_explicit_concatenation(self):
        """Mixed adjacent and + concatenation."""
        source = '__version__ = "1." "0." + "dev"\n'
        assert get_version_from_source(source) == '1.0.dev'

    def test_call_result_skipped(self):
        """Call result assignment is rejected."""
        source = 'from setuptools_scm import get_version\n__version__ = get_version()\n'
        assert get_version_from_source(source) is None

    def test_attribute_skipped(self):
        """Attribute assignment is ignored."""
        source = '__version__ = mod.__version__\n'
        assert get_version_from_source(source) is None

    def test_subscript_skipped(self):
        """Subscript assignment is ignored."""
        source = '__version__ = cfg["version"]\n'
        assert get_version_from_source(source) is None

    def test_complex_expression_skipped(self):
        """Conditional expression is rejected."""
        source = '__version__ = "1.0" if stable else "2.0dev"\n'
        assert get_version_from_source(source) is None

    def test_version_with_module_header(self):
        """Realistic file header with import and version."""
        source = '"""My module."""\nimport sys\n\n__version__ = "4.5.6"\n\nVERSION = __version__\n'
        assert get_version_from_source(source) == '4.5.6'

    def test_first_version_taken(self):
        """Only the first plain version assignment is used as fallback."""
        source = 'version = "1.0"\nversion = "2.0"\n'
        assert get_version_from_source(source) == '1.0'

    def test_empty_source(self):
        """Empty source returns None."""
        source = ''
        assert get_version_from_source(source) is None

    def test_only_comments(self):
        """Source with only comments returns None."""
        source = '# This is a comment\n# __version__ not set\n'
        assert get_version_from_source(source) is None

    def test_syntax_error(self):
        """Invalid Python raises SyntaxError."""
        source = '__version__ = "1.0"\nif True\n'
        with pytest.raises(SyntaxError):
            get_version_from_source(source)

    def test_tuple_unpack_skipped(self):
        """Tuple unpacking assignment is ignored."""
        source = '__version__, _ = "1.0", "extra"\n'
        assert get_version_from_source(source) is None

    def test_list_unpack_skipped(self):
        """List unpacking assignment is ignored."""
        source = '[__version__] = ["1.0"]\n'
        # __version__ is a target in list unpack, not a plain Name target,
        # and it's wrapped in a List pattern, so it should be skipped.
        assert get_version_from_source(source) is None

    def test_version_same_as_dunder(self):
        """version = __version__ = 'xxx' yields 'xxx'."""
        source = 'version = __version__ = "1.0"\n'
        assert get_version_from_source(source) == '1.0'

    def test_dunder_same_as_version(self):
        """__version__ = version = 'xxx' yields 'xxx'."""
        source = '__version__ = version = "2.0"\n'
        assert get_version_from_source(source) == '2.0'

    def test_version_with_typo_dunder(self):
        """version = __verion__ = 'xxx' yields 'xxx' via version fallback."""
        source = 'version = __verion__ = "3.0"\n'
        assert get_version_from_source(source) == '3.0'
