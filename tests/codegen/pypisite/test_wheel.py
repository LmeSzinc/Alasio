"""
Tests for the data of a pip index page: the wheel link and the list of them.
"""
import pytest

from alasio.codegen.pypisite import (
    WheelLink, is_name, normalize_name, prepare_links, split_by_name, split_by_version, unique_links, version_key
)

# the digest of a wheel, the shape the fetcher of the pack server records
DIGEST_A = 'a' * 64
DIGEST_B = 'b' * 64

# the file of a wheel of the tests
FILE = 'httpx2-2.13.1-py3-none-any.whl'


class TestNormalizeName:
    @pytest.mark.parametrize('name, expected', [
        ('httpx2', 'httpx2'),
        ('HTTPX2', 'httpx2'),
        ('ruamel.yaml', 'ruamel-yaml'),
        ('zope__interface', 'zope-interface'),
        ('a.-_b', 'a-b'),
        ('a..b__c--d', 'a-b-c-d'),
    ])
    def test_normalize(self, name, expected):
        """PEP 503: lowercase, runs of '-', '_' and '.' as one '-'."""
        assert normalize_name(name) == expected

    @pytest.mark.parametrize('name, expected', [
        ('httpx2', True),
        ('typing-extensions', True),
        ('a-b-c', True),
        ('', False),
        ('-a', False),
        ('a-', False),
        ('a--b', False),
        ('a_b', False),
        ('a.b', False),
        ('A', False),
    ])
    def test_is_name(self, name, expected):
        assert is_name(name) is expected


class TestVersionKey:
    @pytest.mark.parametrize('versions', [
        ('2.9.0', '2.13.1'),
        ('2.13.1', '2.13.10'),
        ('1.0', '1.0.1'),
        ('0.9', '1.0'),
    ])
    def test_numbers_compare_as_numbers(self, versions):
        """The digit runs compare as numbers, the text order would be wrong."""
        small, big = versions
        assert version_key(small) < version_key(big)

    @pytest.mark.parametrize('versions', [
        # a pre-release comes before the release it precedes
        ('2.0.0rc1', '2.0.0'),
        ('2.0.0a1', '2.0.0b1'),
        ('2.0.0b1', '2.0.0rc1'),
        # a dev release comes before every pre-release of the same version
        ('1.0.dev1', '1.0a1'),
        # a post release comes after the release
        ('1.0', '1.0.post1'),
        # an epoch outranks the release number
        ('2024.1', '1!0.1'),
        # a local version comes after the plain one
        ('1.0', '1.0+local'),
        ('1.0+abc', '1.0+bcd'),
    ])
    def test_pep440_order(self, versions):
        """The order is the one pip resolves a version in, not the text order."""
        small, big = versions
        assert version_key(small) < version_key(big)

    def test_equal(self):
        assert version_key('2.13.1') == version_key('2.13.1')

    def test_equal_across_spellings(self):
        """PEP 440 reads '1.0' and '1.0.0' as one version."""
        assert version_key('1.0') == version_key('1.0.0')

    @pytest.mark.parametrize('version', ['not-a-version', '', '1.0-', 'v1.0x'])
    def test_unparsable_sorts_last(self, version):
        """A version pip cannot resolve is not a candidate, it sorts after the rest."""
        assert version_key('999999999') < version_key(version)

    def test_unparsable_is_total(self):
        """Two unparsable versions still order by their text, a page always renders."""
        assert version_key('aaa') < version_key('bbb')

    def test_same_prefix(self):
        """A version that is the prefix of another sorts before it."""
        assert version_key('1.0') < version_key('1.0.1')


class TestWheelLink:
    def test_default_digest(self):
        assert WheelLink('httpx2', '2.13.1', FILE).sha256 == ''

    def test_key(self):
        assert WheelLink('HTTPX2', '2.13.1', FILE).key == 'httpx2'

    def test_href(self):
        """The page of a distribution links from the version folder below it."""
        assert WheelLink('httpx2', '2.13.1', FILE, DIGEST_A).href() == (
            f'2.13.1/{FILE}#sha256={DIGEST_A}')

    def test_href_with_name(self):
        """The find-links page of a folder links from the folder of the name."""
        assert WheelLink('httpx2', '2.13.1', FILE, DIGEST_A).href(with_name=True) == (
            f'httpx2/2.13.1/{FILE}#sha256={DIGEST_A}')

    def test_href_name_normalized(self):
        assert WheelLink('HTTPX2', '2.13.1', FILE, DIGEST_A).href(with_name=True) == (
            f'httpx2/2.13.1/{FILE}#sha256={DIGEST_A}')

    def test_href_without_digest(self):
        """A link without a digest carries no fragment at all."""
        assert WheelLink('httpx2', '2.13.1', FILE).href() == f'2.13.1/{FILE}'
        assert WheelLink('httpx2', '2.13.1', FILE).href(with_name=True) == f'httpx2/2.13.1/{FILE}'

    @pytest.mark.parametrize('name', [
        '', '   ', '../httpx2', 'httpx2/2', 'a b', '-httpx2', 'httpx2-', 'a+b',
    ])
    def test_invalid_name(self, name):
        with pytest.raises(ValueError, match='not a distribution name'):
            WheelLink(name, '2.13.1', FILE)

    def test_name_with_separators(self):
        """A name written with separators is a name, it normalizes to one."""
        assert WheelLink('a__b', '2.13.1', FILE).key == 'a-b'

    @pytest.mark.parametrize('version', ['', '2.13.1/2', '../2.13.1', '2.13.1#x', '2.13.1?x', '2.13.1\\2'])
    def test_invalid_version(self, version):
        with pytest.raises(ValueError, match='is empty|is not a single url path segment'):
            WheelLink('httpx2', version, FILE)

    @pytest.mark.parametrize('filename', ['', '../x.whl', 'a/x.whl', 'a#x.whl', 'a?x.whl', 'a\\x.whl'])
    def test_invalid_filename(self, filename):
        with pytest.raises(ValueError, match='is empty|is not a single url path segment'):
            WheelLink('httpx2', '2.13.1', filename)

    @pytest.mark.parametrize('digest', ['xyz', 'A' * 64, 'a' * 63, 'a' * 65, 'g' * 64, 'sha256:' + 'a' * 64])
    def test_invalid_digest(self, digest):
        with pytest.raises(ValueError, match='not a lowercase hex digest'):
            WheelLink('httpx2', '2.13.1', FILE, digest)

    def test_frozen(self):
        link = WheelLink('httpx2', '2.13.1', FILE)
        with pytest.raises(AttributeError):
            link.version = '2.13.2'

    def test_repr(self):
        assert repr(WheelLink('httpx2', '2.13.1', FILE)) == f'<WheelLink httpx2==2.13.1 {FILE}>'


class TestUniqueLinks:
    def test_empty(self):
        assert unique_links([]) == []

    def test_order_kept(self):
        """A check of the data is not a page: the order of the caller stays."""
        new = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        old = WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl', DIGEST_A)
        assert unique_links([new, old]) == [new, old]
        assert unique_links([old, new]) == [old, new]

    def test_duplicate_dropped(self):
        link = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert unique_links([link, link]) == [link]

    def test_duplicate_of_another_digest(self):
        with pytest.raises(ValueError, match='two digests'):
            unique_links([
                WheelLink('httpx2', '2.13.1', FILE, DIGEST_A),
                WheelLink('httpx2', '2.13.1', FILE, DIGEST_B),
            ])

    def test_type(self):
        with pytest.raises(TypeError, match='is not a WheelLink'):
            unique_links([('httpx2', '2.13.1', FILE)])


class TestPrepareLinks:
    def test_empty(self):
        assert prepare_links([]) == []

    def test_type(self):
        with pytest.raises(TypeError, match='is not a WheelLink'):
            prepare_links([('httpx2', '2.13.1', FILE)])

    def test_duplicate_dropped(self):
        """A run that read the same wheel twice writes it once."""
        link = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert prepare_links([link, link]) == [link]

    def test_duplicate_of_another_case(self):
        """The name normalizes, so two spellings are one wheel."""
        one = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        other = WheelLink('HTTPX2', '2.13.1', FILE, DIGEST_A)
        assert prepare_links([one, other]) == [one]

    def test_duplicate_of_another_digest(self):
        """One file name of two digests means the source replaced the file."""
        with pytest.raises(ValueError, match='linked with two digests'):
            prepare_links([
                WheelLink('httpx2', '2.13.1', FILE, DIGEST_A),
                WheelLink('httpx2', '2.13.1', FILE, DIGEST_B),
            ])

    def test_sort_by_version(self):
        links = [
            WheelLink('httpx2', '2.13.10', 'httpx2-2.13.10-py3-none-any.whl'),
            WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl'),
            WheelLink('httpx2', '2.13.1', FILE),
        ]
        assert [link.version for link in prepare_links(links)] == ['2.9.0', '2.13.1', '2.13.10']

    def test_sort_by_filename_within_version(self):
        links = [
            WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-py3-none-any.whl', DIGEST_A),
            WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-cp38-cp38-win_amd64.whl', DIGEST_B),
        ]
        assert [link.filename for link in prepare_links(links)] == [
            'httpx2-2.13.1-cp38-cp38-win_amd64.whl',
            'httpx2-2.13.1-py3-none-any.whl',
        ]

    def test_sort_by_name(self):
        links = [
            WheelLink('msgspec', '0.22.0', 'msgspec-0.22.0-py3-none-any.whl', DIGEST_A),
            WheelLink('httpx2', '2.13.1', FILE, DIGEST_B),
        ]
        assert [link.key for link in prepare_links(links, with_name=True)] == ['httpx2', 'msgspec']

    def test_sort_by_name_then_version(self):
        links = [
            WheelLink('httpx2', '2.13.1', FILE, DIGEST_A),
            WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl', DIGEST_A),
        ]
        assert [link.version for link in prepare_links(links, with_name=True)] == ['2.9.0', '2.13.1']

    def test_project_mixed(self):
        """The page of a distribution sits in its folder, a foreign wheel is a link out."""
        links = [
            WheelLink('httpx2', '2.13.1', FILE, DIGEST_A),
            WheelLink('msgspec', '0.22.0', 'msgspec-0.22.0-py3-none-any.whl', DIGEST_A),
        ]
        with pytest.raises(ValueError, match='cannot link the wheel of another distribution'):
            prepare_links(links, project='httpx2')

    def test_project_check_without_project(self):
        """Without a declared project, the links only have to agree with each other."""
        links = [
            WheelLink('httpx2', '2.13.1', FILE, DIGEST_A),
            WheelLink('msgspec', '0.22.0', 'msgspec-0.22.0-py3-none-any.whl', DIGEST_A),
        ]
        with pytest.raises(ValueError, match='cannot link the wheel of another distribution'):
            prepare_links(links)

    def test_with_name_skips_project_check(self):
        """The find-links page of a folder holds every distribution."""
        links = [
            WheelLink('httpx2', '2.13.1', FILE, DIGEST_A),
            WheelLink('msgspec', '0.22.0', 'msgspec-0.22.0-py3-none-any.whl', DIGEST_A),
        ]
        assert len(prepare_links(links, with_name=True, project='httpx2')) == 2

    def test_project_case_insensitive(self):
        links = [WheelLink('HTTPX2', '2.13.1', FILE, DIGEST_A)]
        assert len(prepare_links(links, project='httpx2')) == 1


class TestSplitByName:
    def test_empty(self):
        assert split_by_name([]) == {}

    def test_one_wheel(self):
        link = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert split_by_name([link]) == {'httpx2': [link]}

    def test_two_names(self):
        """Each group is the page of one distribution."""
        one = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        other = WheelLink('msgspec', '0.22.0', 'msgspec-0.22.0-py3-none-any.whl', DIGEST_B)
        assert split_by_name([one, other]) == {'httpx2': [one], 'msgspec': [other]}

    def test_name_normalized(self):
        """The key is the folder of the page, so two spellings are one group."""
        one = WheelLink('ruamel.yaml', '0.18.6', 'ruamel.yaml-0.18.6-py3-none-any.whl', DIGEST_A)
        other = WheelLink('ruamel_yaml', '0.18.6', 'ruamel.yaml-0.18.6-py3-none-any.whl', DIGEST_B)
        assert split_by_name([one, other]) == {'ruamel-yaml': [one, other]}

    def test_versions_grouped(self):
        """Every version of a distribution belongs to its one group."""
        old = WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl', DIGEST_A)
        new = WheelLink('httpx2', '2.13.1', FILE, DIGEST_B)
        assert split_by_name([new, old]) == {'httpx2': [new, old]}

    def test_order_kept(self):
        """A grouping is not a page: the links keep the order they were given in."""
        new = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        old = WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl', DIGEST_A)
        assert split_by_name([new, old])['httpx2'] == [new, old]

    def test_key_order(self):
        """The keys keep the order the names first appear in."""
        one = WheelLink('msgspec', '0.22.0', 'msgspec-0.22.0-py3-none-any.whl', DIGEST_A)
        other = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert list(split_by_name([one, other])) == ['msgspec', 'httpx2']

    def test_duplicates_kept(self):
        """Deduplicating is the step of a page, not of a grouping."""
        link = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert split_by_name([link, link]) == {'httpx2': [link, link]}

    def test_iterable(self):
        """The input is any iterable, not only a list."""
        link = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert split_by_name(iter([link])) == {'httpx2': [link]}

    def test_type(self):
        with pytest.raises(TypeError, match='is not a WheelLink'):
            split_by_name([('httpx2', '2.13.1', FILE)])


class TestSplitByVersion:
    def test_empty(self):
        assert split_by_version([]) == {}

    def test_one_version(self):
        link = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert split_by_version([link]) == {'2.13.1': [link]}

    def test_wheels_of_one_version(self):
        """A release built for several platforms is one version, one table."""
        win = WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-cp38-cp38-win_amd64.whl', DIGEST_A)
        pure = WheelLink('httpx2', '2.13.1', FILE, DIGEST_B)
        assert split_by_version([win, pure]) == {'2.13.1': [win, pure]}

    def test_two_versions(self):
        old = WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl', DIGEST_A)
        new = WheelLink('httpx2', '2.13.1', FILE, DIGEST_B)
        assert split_by_version([old, new]) == {'2.9.0': [old], '2.13.1': [new]}

    def test_key_is_the_exact_version(self):
        """'1.0' and '1.0.0' are two folders, so they are two groups."""
        one = WheelLink('httpx2', '1.0', 'httpx2-1.0-py3-none-any.whl', DIGEST_A)
        other = WheelLink('httpx2', '1.0.0', 'httpx2-1.0.0-py3-none-any.whl', DIGEST_B)
        assert split_by_version([one, other]) == {'1.0': [one], '1.0.0': [other]}

    def test_order_kept(self):
        """A grouping is not a page: the links keep the order they were given in."""
        old = WheelLink('httpx2', '2.9.0', 'httpx2-2.9.0-py3-none-any.whl', DIGEST_A)
        new = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert split_by_version([new, old])['2.13.1'] == [new]
        assert list(split_by_version([new, old])) == ['2.13.1', '2.9.0']

    def test_duplicates_kept(self):
        """Deduplicating is the step of a page, not of a grouping."""
        link = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert split_by_version([link, link]) == {'2.13.1': [link, link]}

    def test_iterable(self):
        """The input is any iterable, not only a list."""
        link = WheelLink('httpx2', '2.13.1', FILE, DIGEST_A)
        assert split_by_version(iter([link])) == {'2.13.1': [link]}

    def test_type(self):
        with pytest.raises(TypeError, match='is not a WheelLink'):
            split_by_version([('httpx2', '2.13.1', FILE)])
