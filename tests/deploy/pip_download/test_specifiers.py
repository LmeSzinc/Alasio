"""
Tests for the PEP 440 specifier port (packaging/specifiers.py).

The cases are the ones an index uses in `data-requires-python`: a minimum
version, a range, a wildcard and an exclusion.
"""
import pytest

from alasio.deploy.pip_download._specifiers import InvalidSpecifier, Specifier, SpecifierSet


class TestSpecifierParse:
    """A specifier is parsed into its operator and its version."""

    @pytest.mark.parametrize('text, operator, version', [
        ('>=3.8', '>=', '3.8'),
        ('>= 3.8', '>=', '3.8'),
        ('==3.8.*', '==', '3.8.*'),
        ('~=3.8.1', '~=', '3.8.1'),
        ('!=3.9.0', '!=', '3.9.0'),
        ('===3.8', '===', '3.8'),
    ])
    def test_parts(self, text, operator, version):
        specifier = Specifier(text)
        assert specifier.operator == operator
        assert specifier.version == version
        assert str(specifier) == f'{operator}{version}'

    @pytest.mark.parametrize('text', [
        '',
        'lolwat',
        '>=',
        '==>1.0',
        '~=1',
        '>=3.8.0+local',
        '*',
        '>=3.8;python_version<"3.9"',
    ])
    def test_invalid(self, text):
        with pytest.raises(InvalidSpecifier):
            Specifier(text)

    def test_equal(self):
        assert Specifier('==1.2.3') == Specifier('==1.2.3.0')
        assert Specifier('>=1.2.3') == '>=1.2.3'
        assert Specifier('>=1.2.3') != Specifier('>1.2.3')


class TestSpecifierContains:
    """The operator comparisons of the specifier."""

    @pytest.mark.parametrize('specifier, version, expected', [
        ('>=3.8', '3.8', True),
        ('>=3.8', '3.7.9', False),
        ('>3.8', '3.8', False),
        ('>3.8', '3.8.1', True),
        # a post release of the spec version is not greater than it
        ('>3.8', '3.8.post1', False),
        ('<=3.8', '3.8.0', True),
        ('<3.9', '3.9.0.dev0', False),
        ('<3.9', '3.8.9', True),
        ('==3.8.*', '3.8.1', True),
        ('==3.8.*', '3.9', False),
        ('==3.8', '3.8.0', True),
        ('==3.8+local', '3.8', False),
        ('==3.8+local', '3.8+local', True),
        ('!=3.9.*', '3.9.1', False),
        ('!=3.9.*', '3.10', True),
        ('~=3.8.1', '3.8.5', True),
        ('~=3.8.1', '3.9', False),
        ('~=3.8', '3.9', True),
        ('===3.8', '3.8', True),
        # a pre-release is not accepted by a specifier that does not name one
        ('>=3.8', '3.9.0rc1', False),
        ('>=3.9.0rc1', '3.9.0rc2', True),
    ])
    def test_contains(self, specifier, version, expected):
        assert (version in Specifier(specifier)) is expected

    def test_prereleases_argument(self):
        assert Specifier('>=3.8').contains('3.9.0rc1', prereleases=True)
        assert not Specifier('>=3.8').contains('3.9.0rc1', prereleases=False)


class TestSpecifierSet:
    """A set contains a version when every specifier of it does."""

    def test_empty(self):
        specifiers = SpecifierSet('')
        assert len(specifiers) == 0
        assert '1.0' in specifiers
        assert '3.9.0rc1' not in specifiers

    def test_contains(self):
        specifiers = SpecifierSet('>=3.6,<3.9')
        assert '3.7' in specifiers
        assert '3.9' not in specifiers
        assert '3.5' not in specifiers

    def test_iter(self):
        specifiers = SpecifierSet('>=3.6,!=3.7.*')
        assert sorted(str(s) for s in specifiers) == ['!=3.7.*', '>=3.6']

    def test_str(self):
        assert str(SpecifierSet('>=3.6,!=3.7.*')) == '!=3.7.*,>=3.6'

    def test_invalid(self):
        with pytest.raises(InvalidSpecifier):
            SpecifierSet('>=3.6,lolwat')

    @pytest.mark.parametrize('text', ['>=3.6,', '>=3.6,,<3.9', ',>=3.6', ','])
    def test_an_empty_specifier_in_the_set(self, text):
        """The text declares a part that is not a specifier: the set is invalid."""
        with pytest.raises(InvalidSpecifier):
            SpecifierSet(text)

    def test_an_empty_set(self):
        """An empty text is the empty set, not a malformed one."""
        assert len(SpecifierSet('')) == 0
        assert len(SpecifierSet('  ')) == 0

    def test_pre_release_of_set(self):
        # one specifier that names a pre-release accepts them for the set
        assert '3.9.0rc1' in SpecifierSet('>=3.9.0rc1')
        assert '3.9.0rc1' not in SpecifierSet('>=3.8')


class TestSpecifierSetAnd:
    """The intersection of two sets, without a text in between."""

    def test_the_non_zero_requires_both(self):
        combined = SpecifierSet('>=1.0') & SpecifierSet('<2.0')

        assert combined == SpecifierSet('>=1.0,<2.0')
        assert '1.5' in combined
        assert '2.0' not in combined
        assert '0.9' not in combined

    def test_an_exclusion_is_kept(self):
        combined = SpecifierSet('>=1.0,<2.0') & SpecifierSet('!=1.4.1')

        assert '1.4.1' not in combined
        assert '1.5' in combined

    def test_a_repeated_specifier_is_one(self):
        combined = SpecifierSet('>=1.0') & SpecifierSet('>=1.0')

        assert len(combined) == 1

    def test_the_empty_set_is_the_identity(self):
        assert SpecifierSet('>=1.0') & SpecifierSet('') == SpecifierSet('>=1.0')

    def test_other_is_not_a_set(self):
        with pytest.raises(TypeError):
            SpecifierSet('>=1.0') & '>=1.0'
