"""
Tests for the versions a compile may pick
(alasio/deploy_dev/compile/candidates.py).
"""
import pytest

from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy_dev.compile import Index, versions_of
from alasio.deploy_dev.compile.candidates import sdist_versions_of
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.compile.conftest import INDEX_URL, MOCK_INDEX_URL

pytestmark = [
    pytest.mark.parallel,
]

# A linux python 3.8 target, whatever runs the suite
TARGET = TargetPython(py_version_info=(3, 8, 20), platforms=['manylinux2014_x86_64'])


def pages_of(network, name, *bases):
    """
    The pages of a name, in the order of the given bases.

    Args:
        network (MockNetwork): The mock network
        name (str): Distribution name
        *bases (str): Base urls, the primary first

    Returns:
        list[tuple[Index, PackageIndex]]: The pages
    """
    out = []
    for position, base in enumerate(bases):
        index = Index.of(base, primary=position == 0)
        out.append((index, _page_of(network, base, name)))
    return out


def _page_of(network, base, name):
    """
    The page of a name, parsed from the mock index.

    Args:
        network (MockNetwork): The mock network
        base (str): Base url of the index
        name (str): Distribution name

    Returns:
        PackageIndex: The page
    """
    from packaging.utils import canonicalize_name

    from alasio.deploy.pip_download._link import parse_links
    from alasio.deploy.pip_download.pypi_file import PackageIndex

    index = network.index(base)
    url = f'{base}/{canonicalize_name(name)}/'
    try:
        body, content_type = index.page(name)
    except KeyError:
        # an index that does not serve the distribution, which the resolver
        # drops from the pages of the name
        body, content_type = b'', 'text/html'
    return PackageIndex(url, body, parse_links(body, url, content_type))


class TestVersionsOf:
    """The versions of a distribution, grouped from its files."""

    def test_the_highest_version_is_the_first(self, network):
        index = network.index()
        index.add('demo', '1.0')
        index.add('demo', '1.2')
        index.add('demo', '1.1')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert [candidate.version for candidate in candidates] == ['1.2', '1.1', '1.0']

    def test_a_version_without_a_wheel_is_an_sdist_candidate(self, network):
        """A version that only publishes an sdist is installable: pip builds it."""
        index = network.index()
        index.add('demo', '1.0')
        index.add('demo', '2.0', filename='demo-2.0.tar.gz')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert [(candidate.version, candidate.sdist) for candidate in candidates] == [
            ('2.0', True), ('1.0', False)]
        assert candidates[0].wheels == ()
        assert sdist_versions_of(
            'demo', pages_of(network, 'demo', INDEX_URL)) == {'2.0'}

    def test_a_wheel_of_another_platform_is_not_a_candidate(self, network):
        index = network.index()
        index.add('demo', '1.0', filename='demo-1.0-cp38-cp38-win_amd64.whl')
        index.add('demo', '1.1')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert [candidate.version for candidate in candidates] == ['1.1']

    def test_a_wheel_of_another_interpreter_is_not_a_candidate(self, network):
        index = network.index()
        index.add('demo', '1.0', filename='demo-1.0-cp311-cp311-manylinux2014_x86_64.whl')
        index.add('demo', '1.1')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert [candidate.version for candidate in candidates] == ['1.1']

    def test_a_requires_python_the_target_does_not_satisfy(self, network):
        index = network.index()
        index.add('demo', '1.0', requires_python='>=3.9')
        index.add('demo', '1.1')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert [candidate.version for candidate in candidates] == ['1.1']

    def test_a_yanked_version_is_not_a_candidate(self, network):
        index = network.index()
        index.add('demo', '1.0', yanked='broken')
        index.add('demo', '1.1')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert [candidate.version for candidate in candidates] == ['1.1']

    def test_the_pure_wheel_is_the_first_file(self, network):
        index = network.index()
        index.add('demo', '1.0', filename='demo-1.0-cp38-cp38-manylinux2014_x86_64.whl')
        index.add('demo', '1.0', filename='demo-1.0-py3-none-any.whl')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert candidates[0].wheels[0] == 'demo-1.0-py3-none-any.whl'
        assert candidates[0].pure is True

    def test_a_compiled_only_version_is_not_pure(self, network):
        index = network.index()
        index.add('demo', '1.0', filename='demo-1.0-cp38-cp38-manylinux2014_x86_64.whl')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert candidates[0].pure is False

    def test_the_first_index_serves_a_version(self, network):
        """A version both indexes publish is served by the first one."""
        network.index(INDEX_URL).add('demo', '1.0')
        network.index(MOCK_INDEX_URL).add('demo', '1.0')

        candidates = versions_of(
            'demo', pages_of(network, 'demo', INDEX_URL, MOCK_INDEX_URL), TARGET)

        assert candidates[0].source == INDEX_URL

    def test_the_second_index_supplies_what_the_first_cannot(self, network):
        """A version the first index only publishes for another python."""
        network.index(INDEX_URL).add(
            'demo', '1.0', filename='demo-1.0-cp311-cp311-manylinux2014_x86_64.whl')
        network.index(MOCK_INDEX_URL).add('demo', '1.0')

        candidates = versions_of(
            'demo', pages_of(network, 'demo', INDEX_URL, MOCK_INDEX_URL), TARGET)

        assert [candidate.version for candidate in candidates] == ['1.0']
        assert candidates[0].source == MOCK_INDEX_URL

    def test_a_distribution_the_index_does_not_serve(self, network):
        assert versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET) == []

    def test_a_version_that_is_not_pep440(self, network):
        index = network.index()
        index.add('demo', 'not-a-version')

        assert versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET) == []

    def test_a_file_of_another_distribution_is_ignored(self, network):
        index = network.index()
        index.add('demo', '1.0')
        index.add('other', '1.0')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert [candidate.version for candidate in candidates] == ['1.0']

    def test_a_prerelease_is_a_candidate(self, network):
        """The filter of the prereleases is the decision of the resolver."""
        index = network.index()
        index.add('demo', '2.0rc1')

        candidates = versions_of('demo', pages_of(network, 'demo', INDEX_URL), TARGET)

        assert candidates[0].version == '2.0rc1'
        assert candidates[0].prerelease is True
