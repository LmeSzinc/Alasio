"""
Tests for the resolver of a compile (alasio/deploy_dev/compile/resolver.py).

The index is the mock of tests/deploy_dev/pypi_mock.py: every test states the
versions of the dependencies and their Requires-Dist, and the resolver decides.
"""
import pytest

from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy_dev.compile import CompileError, Index, RequirementSpec, Resolver
from alasio.deploy_dev.compile.resolver import PROJECT_ORIGIN
from alasio.deploy_dev.metadata import MetadataProvider
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.compile.conftest import INDEX_URL, MOCK_INDEX_URL
from tests.deploy_dev.pypi_mock import make_sdist

pytestmark = [
    pytest.mark.parallel,
]

# A linux python 3.8 target, whatever runs the suite
TARGET = TargetPython(py_version_info=(3, 8, 20), platforms=['manylinux2014_x86_64'])


def root(name, specifier='', extras=()):
    """
    A requirement the project declares itself.

    Args:
        name (str): Distribution name
        specifier (str): PEP 440 constraint. Defaults to ''
        extras (Iterable[str]): Extras. Defaults to ()

    Returns:
        RequirementSpec: The requirement
    """
    return RequirementSpec(
        name=name, extras=tuple(extras), specifier=specifier, origin=PROJECT_ORIGIN)


def resolver_of(network, existing=None, resolution='highest', sources=None, target=TARGET,
                indexes=None):
    """
    A resolver over the mock network.

    Args:
        network (MockNetwork): The mock network
        existing (dict[str, str] | None): The pins of a previous lock
        resolution (str): 'highest' or 'lowest'
        sources (dict[str, str] | None): The index of a name the project names
        target (TargetPython): The interpreter to compile for
        indexes (list[Index] | None): The indexes, the primary first

    Returns:
        Resolver: The resolver
    """
    provider = MetadataProvider(client=network.client, concurrency=4)
    if indexes is None:
        indexes = [Index.of(INDEX_URL, primary=True)]
    return Resolver(
        provider, indexes, target, existing=existing, resolution=resolution, sources=sources)


class TestResolve:
    """The pins a resolver decides."""

    @pytest.mark.trio
    async def test_a_single_requirement(self, network):
        network.index().add('demo', '1.0')

        resolution = await resolver_of(network).resolve([root('demo')])

        assert resolution.pins == {'demo': '1.0'}
        assert resolution.sources == {'demo': INDEX_URL}
        assert resolution.parents == {'demo': (PROJECT_ORIGIN,)}

    @pytest.mark.trio
    async def test_the_highest_version_is_picked(self, network):
        index = network.index()
        index.add('demo', '1.0')
        index.add('demo', '1.2')
        index.add('demo', '1.1')

        resolution = await resolver_of(network).resolve([root('demo')])

        assert resolution.pins == {'demo': '1.2'}

    @pytest.mark.trio
    async def test_a_constraint_is_applied(self, network):
        index = network.index()
        index.add('demo', '1.0')
        index.add('demo', '2.0')

        resolution = await resolver_of(network).resolve([root('demo', '<2')])

        assert resolution.pins == {'demo': '1.0'}

    @pytest.mark.trio
    async def test_the_lowest_version_is_picked(self, network):
        index = network.index()
        index.add('demo', '1.0')
        index.add('demo', '1.2')

        resolution = await resolver_of(network, resolution='lowest').resolve([root('demo')])

        assert resolution.pins == {'demo': '1.0'}

    @pytest.mark.trio
    async def test_the_existing_pin_is_preferred(self, network):
        index = network.index()
        index.add('demo', '1.0')
        index.add('demo', '1.2')

        resolution = await resolver_of(network, existing={'demo': '1.0'}).resolve([root('demo')])

        assert resolution.pins == {'demo': '1.0'}

    @pytest.mark.trio
    async def test_the_existing_pin_is_dropped_when_it_does_not_satisfy(self, network):
        index = network.index()
        index.add('demo', '1.0')
        index.add('demo', '1.2')

        resolution = await resolver_of(
            network, existing={'demo': '1.0'}).resolve([root('demo', '>=1.2')])

        assert resolution.pins == {'demo': '1.2'}

    @pytest.mark.trio
    async def test_a_prerelease_is_only_picked_when_it_is_named(self, network):
        index = network.index()
        index.add('demo', '1.0')
        index.add('demo', '2.0rc1')

        resolution = await resolver_of(network).resolve([root('demo')])
        assert resolution.pins == {'demo': '1.0'}

        resolution = await resolver_of(network).resolve([root('demo', '>=2.0rc1')])
        assert resolution.pins == {'demo': '2.0rc1'}


class TestDependencies:
    """The closure the resolver walks."""

    @pytest.mark.trio
    async def test_a_dependency_chain(self, network):
        index = network.index()
        index.add('demo', '1.0', requires=['middle>=1'])
        index.add('middle', '1.5', requires=['leaf'])
        index.add('leaf', '3.0')

        resolution = await resolver_of(network).resolve([root('demo')])

        assert resolution.pins == {'demo': '1.0', 'middle': '1.5', 'leaf': '3.0'}
        assert resolution.parents['middle'] == ('demo',)
        assert resolution.parents['leaf'] == ('middle',)

    @pytest.mark.trio
    async def test_a_name_two_parents_ask_for(self, network):
        index = network.index()
        index.add('demo', '1.0', requires=['shared'])
        index.add('other', '1.0', requires=['shared'])
        index.add('shared', '2.0')

        resolution = await resolver_of(network).resolve([root('demo'), root('other')])

        assert resolution.parents['shared'] == ('demo', 'other')

    @pytest.mark.trio
    async def test_the_constraints_of_two_parents_are_intersected(self, network):
        index = network.index()
        index.add('demo', '1.0', requires=['shared<2'])
        index.add('other', '1.0', requires=['shared>=1.5'])
        index.add('shared', '1.0')
        index.add('shared', '1.9')
        index.add('shared', '2.5')

        resolution = await resolver_of(network).resolve([root('demo'), root('other')])

        assert resolution.pins['shared'] == '1.9'

    @pytest.mark.trio
    async def test_the_extras_of_a_requirement_are_expanded(self, network):
        index = network.index()
        index.add('demo', '1.0', requires=['http2; extra == "http2"'])
        index.add('http2', '1.0')

        resolution = await resolver_of(network).resolve([root('demo', extras=['http2'])])

        assert set(resolution.pins) == {'demo', 'http2'}
        assert resolution.extras['demo'] == ('http2',)

    @pytest.mark.trio
    async def test_a_dependency_of_an_extra_that_was_not_asked_for(self, network):
        index = network.index()
        index.add('demo', '1.0', requires=['http2; extra == "http2"'])
        index.add('http2', '1.0')

        resolution = await resolver_of(network).resolve([root('demo')])

        assert set(resolution.pins) == {'demo'}

    @pytest.mark.trio
    async def test_a_dependency_of_another_platform(self, network):
        index = network.index()
        index.add('demo', '1.0', requires=['winonly; sys_platform == "cygwin"'])
        index.add('winonly', '1.0')

        resolution = await resolver_of(network).resolve([root('demo')])

        assert set(resolution.pins) == {'demo'}


class TestBacktracking:
    """The choices the resolver takes back when a constraint fails."""

    @pytest.mark.trio
    async def test_a_transitive_constraint_takes_back_the_version(self, network):
        """The newest demo conflicts with what other requires, an older one does not."""
        index = network.index()
        index.add('demo', '2.0', requires=['shared>=2'])
        index.add('demo', '1.0', requires=['shared<2'])
        index.add('other', '1.0', requires=['shared<2'])
        index.add('shared', '1.0')
        index.add('shared', '2.0')

        resolution = await resolver_of(network).resolve([root('demo'), root('other')])

        assert resolution.pins['demo'] == '1.0'
        assert resolution.pins['shared'] == '1.0'

    @pytest.mark.trio
    async def test_a_version_whose_metadata_cannot_be_read_is_skipped(self, network):
        index = network.index()
        index.add('demo', '2.0', content=b'not a wheel')
        index.add('demo', '1.0')

        resolution = await resolver_of(network).resolve([root('demo')])

        assert resolution.pins == {'demo': '1.0'}

    @pytest.mark.trio
    async def test_a_constraint_that_no_version_satisfies(self, network):
        index = network.index()
        index.add('demo', '1.0')

        with pytest.raises(CompileError, match='No version of "demo" matches ">=2"'):
            await resolver_of(network).resolve([root('demo', '>=2')])

    @pytest.mark.trio
    async def test_the_chain_of_a_conflict_is_reported(self, network):
        index = network.index()
        index.add('demo', '1.0', requires=['shared>=5'])
        index.add('shared', '1.0')

        with pytest.raises(CompileError) as info:
            await resolver_of(network).resolve([root('demo')])

        message = str(info.value)
        assert 'No version of "shared" matches ">=5"' in message
        assert 'required by: demo -> shared>=5' in message
        assert 'candidates: 1.0' in message

    @pytest.mark.trio
    async def test_a_distribution_no_index_serves(self, network):
        with pytest.raises(CompileError, match='No version of "missing"'):
            await resolver_of(network).resolve([root('missing')])

    @pytest.mark.trio
    async def test_a_version_that_only_has_an_sdist(self, network):
        """A version that publishes no wheel: pip builds the sdist, so it is a candidate."""
        index = network.index()
        index.add('demo', '1.0', filename='demo-1.0.tar.gz',
                  content=make_sdist('demo', '1.0', ['leaf']))
        index.add('leaf', '1.0')

        resolution = await resolver_of(network).resolve([root('demo')])

        assert resolution.pins == {'demo': '1.0', 'leaf': '1.0'}

    @pytest.mark.trio
    async def test_an_sdist_that_declares_no_dependency(self, network):
        """A legacy sdist whose dependencies are only known to the build fails."""
        index = network.index()
        index.add('demo', '1.0', filename='demo-1.0.tar.gz',
                  content=make_sdist('demo', '1.0', metadata_version='1.1'))

        with pytest.raises(CompileError) as info:
            await resolver_of(network).resolve([root('demo')])

        message = str(info.value)
        assert 'declares no static dependency metadata' in message
        assert 'source distribution only: 1.0' in message

    @pytest.mark.trio
    async def test_the_search_is_bounded(self, network):
        """A conflict that needs a backtrack the budget does not allow fails cleanly."""
        index = network.index()
        index.add('demo', '2.0', requires=['shared>=2'])
        index.add('demo', '1.0', requires=['shared<2'])
        index.add('other', '1.0', requires=['shared<2'])
        index.add('shared', '1.0')
        index.add('shared', '2.0')
        provider = MetadataProvider(client=network.client, concurrency=4)
        resolver = Resolver(
            provider, [Index.of(INDEX_URL, primary=True)], TARGET, max_backtracks=0)

        with pytest.raises(CompileError, match='backtracking steps were spent'):
            await resolver.resolve([root('demo'), root('other')])


class TestIndexes:
    """The index a pin is read from."""

    @pytest.mark.trio
    async def test_an_extra_index_serves_a_version_both_publish(self, network):
        """The extra index is preferred: it is the one the project declares."""
        network.index(INDEX_URL).add('demo', '1.0')
        network.index(MOCK_INDEX_URL).add('demo', '1.0')

        resolution = await resolver_of(network, indexes=[
            Index.of(INDEX_URL, primary=True), Index.of(MOCK_INDEX_URL),
        ]).resolve([root('demo')])

        assert resolution.pins == {'demo': '1.0'}
        assert resolution.sources == {'demo': MOCK_INDEX_URL}

    @pytest.mark.trio
    async def test_the_primary_index_serves_what_the_extra_one_lacks(self, network):
        network.index(INDEX_URL).add('demo', '1.0')
        network.index(MOCK_INDEX_URL).add('other', '2.0')

        resolution = await resolver_of(network, indexes=[
            Index.of(INDEX_URL, primary=True), Index.of(MOCK_INDEX_URL),
        ]).resolve([root('demo'), root('other')])

        assert resolution.sources == {'demo': INDEX_URL, 'other': MOCK_INDEX_URL}

    @pytest.mark.trio
    async def test_the_highest_version_wins_over_the_preference(self, network):
        """The versions are compared across the indexes, then the source is chosen."""
        network.index(INDEX_URL).add('demo', '2.0')
        network.index(MOCK_INDEX_URL).add('demo', '1.0')

        resolution = await resolver_of(network, indexes=[
            Index.of(INDEX_URL, primary=True), Index.of(MOCK_INDEX_URL),
        ]).resolve([root('demo')])

        assert resolution.pins == {'demo': '2.0'}
        assert resolution.sources == {'demo': INDEX_URL}

    @pytest.mark.trio
    async def test_the_second_index_supplies_the_interpreter(self, network):
        network.index(INDEX_URL).add(
            'demo', '2.0', filename='demo-2.0-cp311-cp311-manylinux2014_x86_64.whl')
        network.index(MOCK_INDEX_URL).add('demo', '2.0')
        network.index(INDEX_URL).add('demo', '1.0')

        resolution = await resolver_of(network, indexes=[
            Index.of(INDEX_URL, primary=True), Index.of(MOCK_INDEX_URL),
        ]).resolve([root('demo')])

        assert resolution.pins == {'demo': '2.0'}
        assert resolution.sources == {'demo': MOCK_INDEX_URL}

    @pytest.mark.trio
    async def test_a_named_source_is_the_only_one_read(self, network):
        network.index(INDEX_URL).add('demo', '1.0')
        network.index(MOCK_INDEX_URL).add('demo', '0.9')

        resolution = await resolver_of(
            network,
            indexes=[Index.of(INDEX_URL, primary=True), Index.of(MOCK_INDEX_URL)],
            sources={'demo': MOCK_INDEX_URL},
        ).resolve([root('demo')])

        assert resolution.pins == {'demo': '0.9'}
        assert resolution.sources == {'demo': MOCK_INDEX_URL}

    @pytest.mark.trio
    async def test_a_page_is_read_once(self, network):
        index = network.index()
        index.add('demo', '1.0', requires=['shared', 'other'])
        index.add('shared', '1.0')
        index.add('other', '1.0', requires=['shared'])

        await resolver_of(network).resolve([root('demo')])

        pages = [url for url in network.requests if url.endswith('/')]
        assert len(pages) == len(set(pages))
