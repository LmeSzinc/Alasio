"""
Tests for the metadata provider of the developer tools
(alasio/deploy_dev/metadata/provider.py).

The index is the mock of tests/deploy_dev/pypi_mock.py, which serves the pages,
the files, the byte ranges and the PEP 658 metadata file of a wheel, and can be
told to behave like the two hosts a run meets: PyPI (a suffix range) and the S3
host of a GitHub release asset (no suffix range, 501).
"""
import os

import pytest
import trio

from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy.pip_download.pypi_file import WheelNotFoundError
from alasio.deploy_dev.metadata import (
    MODE_FULL, MODE_PEP658, MODE_RANGE, MODE_RANGE_NO_SUFFIX, ClosureRoot, MetadataError, MetadataProvider,
    SourceCapabilities, readable_wheels
)
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.metadata.conftest import INDEX_URL
from tests.deploy_dev.pypi_mock import make_wheel

pytestmark = [
    pytest.mark.parallel,
]

# The target of the tests: a fixed python 3.8, whatever runs the suite
TARGET = TargetPython(py_version_info=(3, 8, 20))

PURE_WHEEL = 'demo-1.0-py3-none-any.whl'
COMPILED_WHEEL = 'demo-1.0-cp38-cp38-win_amd64.whl'


def wheel_requests(index):
    """
    The requests that transferred a wheel, the metadata file excluded.

    Args:
        index (MockIndex): The mock index

    Returns:
        list[str]: Urls of the requests, in order
    """
    return [url for url in index.requests if url.endswith('.whl')]


class TestPep658:
    """The metadata file next to a wheel, the cheapest way to read it."""

    @pytest.mark.trio
    async def test_the_metadata_file_is_read(self, index):
        index.add('demo', '1.0', requires=['sniffio'], metadata='sha256')
        provider = MetadataProvider(client=index.client)

        meta = await provider.metadata('demo', '1.0', INDEX_URL)

        assert meta.name == 'demo'
        assert meta.version == '1.0'
        assert meta.mode == MODE_PEP658
        assert meta.requires_dist == ['sniffio']
        assert meta.origin.endswith(f'{PURE_WHEEL}.metadata')
        assert meta.source == f'{INDEX_URL}/demo/'
        # the wheel itself is never transferred
        assert wheel_requests(index) == []
        await provider.aclose()

    @pytest.mark.trio
    async def test_the_declaration_without_a_hash(self, index):
        """`data-dist-info-metadata="true"` names a file no hash checks."""
        index.add('demo', '1.0', requires=['sniffio'], metadata='true')
        provider = MetadataProvider(client=index.client)

        meta = await provider.metadata('demo', '1.0', INDEX_URL)

        assert meta.mode == MODE_PEP658
        assert meta.requires_dist == ['sniffio']
        await provider.aclose()

    @pytest.mark.trio
    async def test_an_index_that_declares_but_does_not_serve(self, index):
        """The file of a wheel is missing: that wheel is read, not the source."""
        index.serve_metadata = False
        index.add('demo', '1.0', requires=['sniffio'], metadata='true',
                  content=make_wheel('demo', '1.0', ['sniffio']))
        provider = MetadataProvider(client=index.client)

        meta = await provider.metadata('demo', '1.0', INDEX_URL)

        assert meta.mode == MODE_RANGE
        assert meta.requires_dist == ['sniffio']
        await provider.aclose()


class TestRange:
    """The METADATA member of a wheel, read from the bytes the host serves."""

    @pytest.mark.trio
    async def test_the_tail_holds_the_member(self, index):
        """A small wheel is one range request after the capability probe."""
        index.add('demo', '1.0', requires=['sniffio'],
                  content=make_wheel('demo', '1.0', ['sniffio']))
        provider = MetadataProvider(client=index.client)

        meta = await provider.metadata('demo', '1.0', INDEX_URL)

        assert meta.mode == MODE_RANGE
        assert meta.requires_dist == ['sniffio']
        assert meta.origin.endswith(PURE_WHEEL)
        # the probe, then the tail: the member is in the tail
        assert len(wheel_requests(index)) == 2
        await provider.aclose()

    @pytest.mark.trio
    async def test_the_member_is_outside_the_tail(self, index):
        """A wheel whose METADATA is followed by a long RECORD costs a window."""
        index.add('demo', '1.0', requires=['sniffio'],
                  content=make_wheel('demo', '1.0', ['sniffio'], trail=os.urandom(32768)))
        provider = MetadataProvider(client=index.client)

        meta = await provider.metadata('demo', '1.0', INDEX_URL)

        assert meta.mode == MODE_RANGE
        assert meta.requires_dist == ['sniffio']
        # the probe, the tail, then the window of the member
        assert len(wheel_requests(index)) == 3
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_source_without_the_suffix_range(self, index):
        """The host of a GitHub release asset: 501 to the tail, 206 to a range."""
        index.suffix_range_supported = False
        index.add('demo', '1.0', requires=['sniffio'],
                  content=make_wheel('demo', '1.0', ['sniffio']))
        provider = MetadataProvider(client=index.client)

        meta = await provider.metadata('demo', '1.0', INDEX_URL)
        caps = await provider.capabilities(INDEX_URL)

        assert meta.mode == MODE_RANGE_NO_SUFFIX
        assert meta.requires_dist == ['sniffio']
        assert (caps.suffix_range, caps.range) == (False, True)
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_refused_suffix_range_downgrades_the_version_only(self, index):
        """A source whose capabilities are unknown: the 501 is this file's answer.

        The probe of a source may have failed on the network, leaving the
        source unknown, which reads as "try the suffix range". A host that
        refuses it answers 501, and that must fall back to the explicit range
        of this version: it must not fail the read, and it must not degrade the
        source either (another file of the index may be served by another host).
        """
        index.suffix_range_supported = False
        index.add('demo', '1.0', requires=['sniffio'],
                  content=make_wheel('demo', '1.0', ['sniffio']))
        provider = MetadataProvider(client=index.client)
        # the state of a source whose probe never answered
        state = provider._capabilities.setdefault(INDEX_URL, SourceCapabilities())
        state.ready = True

        meta = await provider.metadata('demo', '1.0', INDEX_URL)

        assert meta.mode == MODE_RANGE_NO_SUFFIX
        assert meta.requires_dist == ['sniffio']
        # the source is still unknown: only the probe writes it
        assert (state.suffix_range, state.range) == (None, None)
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_source_without_any_range(self, index):
        """A host that ignores the header: the whole wheel, the fallback."""
        index.range_supported = False
        index.add('demo', '1.0', requires=['sniffio'],
                  content=make_wheel('demo', '1.0', ['sniffio']))
        provider = MetadataProvider(client=index.client)

        meta = await provider.metadata('demo', '1.0', INDEX_URL)
        caps = await provider.capabilities(INDEX_URL)

        assert meta.mode == MODE_FULL
        assert meta.requires_dist == ['sniffio']
        assert (caps.suffix_range, caps.range) == (False, False)
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_wheel_that_is_not_an_archive(self, index):
        index.add('demo', '1.0', content=b'not a wheel')
        provider = MetadataProvider(client=index.client)

        with pytest.raises(MetadataError, match='Cannot read the metadata of "demo==1.0"'):
            await provider.metadata('demo', '1.0', INDEX_URL)
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_version_without_a_wheel(self, index):
        index.add('demo', '1.0')
        provider = MetadataProvider(client=index.client)

        with pytest.raises(WheelNotFoundError):
            await provider.metadata('demo', '2.0', INDEX_URL)
        await provider.aclose()


class TestCaching:
    """One run reads every page and every METADATA once."""

    @pytest.mark.trio
    async def test_the_page_is_read_once(self, index):
        index.add('demo', '1.0', metadata='sha256')
        provider = MetadataProvider(client=index.client)

        first = await provider.page('demo', INDEX_URL)
        second = await provider.page('demo', INDEX_URL)

        assert first is second
        assert len([url for url in index.requests if url.endswith('/demo/')]) == 1
        await provider.aclose()

    @pytest.mark.trio
    async def test_the_metadata_is_read_once(self, index):
        index.add('demo', '1.0', requires=['sniffio'], metadata='sha256')
        provider = MetadataProvider(client=index.client)

        first = await provider.metadata('demo', '1.0', INDEX_URL)
        before = list(index.requests)
        second = await provider.metadata('demo', '1.0', INDEX_URL)

        assert first is second
        assert index.requests == before
        await provider.aclose()

    @pytest.mark.trio
    async def test_concurrent_callers_share_a_read(self, index):
        index.add('demo', '1.0', requires=['sniffio'], metadata='sha256')
        provider = MetadataProvider(client=index.client)
        got = []

        async def read():
            got.append(await provider.metadata('demo', '1.0', INDEX_URL))

        async with trio.open_nursery() as nursery:
            for _ in range(4):
                nursery.start_soon(read)

        assert len(got) == 4
        assert got[0] is got[1]
        # one page and one metadata file, whatever the number of callers
        assert len([url for url in index.requests if url.endswith('/demo/')]) == 1
        assert len([url for url in index.requests if url.endswith('.metadata')]) == 1
        await provider.aclose()


class TestCapabilities:
    """What a source is known to serve, probed once per run and per source."""

    @pytest.mark.trio
    async def test_the_probe_is_one_request_per_source(self, index):
        index.add('demo', '1.0', content=make_wheel('demo', '1.0'))
        provider = MetadataProvider(client=index.client)
        page = await provider.page('demo', INDEX_URL)
        candidates = readable_wheels(page.links, 'demo', '1.0')

        first = await provider.capabilities(INDEX_URL, candidates)
        second = await provider.capabilities(INDEX_URL, candidates)

        assert first is second
        assert (first.suffix_range, first.range, first.ready) == (True, True, True)
        assert len(wheel_requests(index)) == 1
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_probe_that_learned_nothing_is_retried(self, index):
        """A probe without a file learns nothing, the next caller asks again."""
        index.add('demo', '1.0', content=make_wheel('demo', '1.0'))
        provider = MetadataProvider(client=index.client)

        unknown = await provider.capabilities(INDEX_URL)
        assert unknown.ready is False

        page = await provider.page('demo', INDEX_URL)
        known = await provider.capabilities(INDEX_URL, readable_wheels(page.links, 'demo', '1.0'))

        assert known is unknown
        assert known.ready is True
        assert (known.suffix_range, known.range) == (True, True)
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_failed_read_does_not_degrade_the_source(self, index):
        """A broken wheel of one version is not a capability of the host."""
        index.add('demo', '1.0', content=b'not a wheel')
        index.add('demo', '2.0', content=make_wheel('demo', '2.0'))
        provider = MetadataProvider(client=index.client)

        with pytest.raises(MetadataError):
            await provider.metadata('demo', '1.0', INDEX_URL)
        caps = await provider.capabilities(INDEX_URL)

        assert (caps.suffix_range, caps.range) == (True, True)
        await provider.aclose()

    @pytest.mark.trio
    async def test_the_note_is_per_version(self, index):
        """A version that could not be read is not retried on another mode."""
        index.add('demo', '1.0', content=b'not a wheel')
        provider = MetadataProvider(client=index.client)

        with pytest.raises(MetadataError):
            await provider.metadata('demo', '1.0', INDEX_URL)

        notes = provider._notes[(INDEX_URL, 'demo', '1.0')]
        assert MODE_RANGE in notes
        assert MODE_FULL in notes
        await provider.aclose()


class TestReadableWheels:
    """The order the wheels of a version are read in."""

    @pytest.mark.parametrize('tag', ['cp38-cp38-win_amd64'])
    def test_a_pure_wheel_is_read_first(self, index, tag):
        index.add('demo', '1.0', filename=COMPILED_WHEEL)
        index.add('demo', '1.0', filename=PURE_WHEEL)

        candidates = readable_wheels(index.links('demo'), 'demo', '1.0')

        assert [candidate.filename for candidate in candidates] == [PURE_WHEEL, COMPILED_WHEEL]

    def test_a_yanked_wheel_is_read_last(self, index):
        index.add('demo', '1.0', filename='demo-1.0-py2.py3-none-any.whl', yanked='broken')
        index.add('demo', '1.0', filename=PURE_WHEEL)

        candidates = readable_wheels(index.links('demo'), 'demo', '1.0')

        assert [candidate.filename for candidate in candidates] == [
            PURE_WHEEL, 'demo-1.0-py2.py3-none-any.whl']

    def test_a_version_without_a_wheel(self, index):
        index.add('demo', '1.0')

        with pytest.raises(WheelNotFoundError):
            readable_wheels(index.links('demo'), 'demo', '2.0')


class TestClosure:
    """The dependency closure of the registered roots, walked at once."""

    @staticmethod
    def resolve_of(pins):
        """
        The pin lookup of a closure walk.

        Args:
            pins (dict[str, str]): {name: version}, every pin from the mock index

        Returns:
            Callable[[str], tuple[str, str] | None]: The lookup
        """
        return lambda name: (pins[name], INDEX_URL) if name in pins else None

    @pytest.mark.trio
    async def test_the_walk_expands_dependencies(self, index):
        index.add('demo', '1.0', requires=['pure>=2'], metadata='sha256')
        index.add('pure', '2.0', metadata='sha256')
        provider = MetadataProvider(client=index.client)

        result = await provider.closure(
            [ClosureRoot('demo')], self.resolve_of({'demo': '1.0', 'pure': '2.0'}), TARGET)

        assert result.names == ['demo', 'pure']
        assert set(result.metadata) == {'demo', 'pure'}
        assert result.metadata['demo'].requires_dist == ['pure>=2']
        assert (result.missing, result.errors) == ([], {})
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_dependency_outside_the_lock_is_missing(self, index):
        index.add('demo', '1.0', requires=['outside>=1'], metadata='sha256')
        provider = MetadataProvider(client=index.client)

        result = await provider.closure([ClosureRoot('demo')], self.resolve_of({'demo': '1.0'}), TARGET)

        assert result.names == ['demo']
        assert result.missing == ['outside']
        await provider.aclose()

    @pytest.mark.trio
    async def test_the_extras_of_a_child_are_expanded(self, index):
        index.add('demo', '1.0', requires=['pure[http2]'], metadata='sha256')
        index.add('pure', '2.0', requires=['h2; extra == "http2"'], metadata='sha256')
        index.add('h2', '3.0', metadata='sha256')
        provider = MetadataProvider(client=index.client)
        pins = {'demo': '1.0', 'pure': '2.0', 'h2': '3.0'}

        result = await provider.closure([ClosureRoot('demo')], self.resolve_of(pins), TARGET)

        assert result.names == ['demo', 'pure', 'h2']
        await provider.aclose()

    @pytest.mark.trio
    async def test_an_extra_that_nobody_asked_is_not_expanded(self, index):
        index.add('demo', '1.0', requires=['pure'], metadata='sha256')
        index.add('pure', '2.0', requires=['h2; extra == "http2"'], metadata='sha256')
        index.add('h2', '3.0', metadata='sha256')
        provider = MetadataProvider(client=index.client)
        pins = {'demo': '1.0', 'pure': '2.0', 'h2': '3.0'}

        result = await provider.closure([ClosureRoot('demo')], self.resolve_of(pins), TARGET)

        assert result.names == ['demo', 'pure']
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_root_that_is_not_recursive(self, index):
        index.add('demo', '1.0', requires=['pure>=2'], metadata='sha256')
        index.add('pure', '2.0', metadata='sha256')
        provider = MetadataProvider(client=index.client)

        result = await provider.closure(
            [ClosureRoot('demo', recursive=False)],
            self.resolve_of({'demo': '1.0', 'pure': '2.0'}), TARGET)

        assert result.names == ['demo']
        assert result.metadata == {}
        await provider.aclose()

    @pytest.mark.trio
    async def test_an_unreadable_metadata_is_an_error(self, index):
        index.add('demo', '1.0', requires=['pure>=2'], content=b'not a wheel')
        index.add('pure', '2.0', metadata='sha256')
        provider = MetadataProvider(client=index.client)

        result = await provider.closure(
            [ClosureRoot('demo')], self.resolve_of({'demo': '1.0', 'pure': '2.0'}), TARGET)

        assert list(result.errors) == ['demo']
        assert result.names == ['demo']
        await provider.aclose()

    @pytest.mark.trio
    async def test_a_name_reached_twice_is_read_once(self, index):
        index.add('demo', '1.0', requires=['pure', 'other'], metadata='sha256')
        index.add('other', '1.0', requires=['pure'], metadata='sha256')
        index.add('pure', '2.0', metadata='sha256')
        provider = MetadataProvider(client=index.client)
        pins = {'demo': '1.0', 'other': '1.0', 'pure': '2.0'}

        result = await provider.closure([ClosureRoot('demo')], self.resolve_of(pins), TARGET)

        assert set(result.metadata) == {'demo', 'other', 'pure'}
        assert len([url for url in index.requests if url.endswith('.metadata')
                    and '/pure/' in url]) == 1
        await provider.aclose()


class TestIndexShapes:
    """The answers of an index the reader meets in the wild."""

    @pytest.mark.trio
    async def test_a_distribution_with_several_versions(self, index):
        index.add('demo', '1.0', requires=['old'], metadata='sha256')
        index.add('demo', '2.0', requires=['new'], metadata='sha256')
        provider = MetadataProvider(client=index.client)

        old = await provider.metadata('demo', '1.0', INDEX_URL)
        new = await provider.metadata('demo', '2.0', INDEX_URL)

        assert (old.requires_dist, new.requires_dist) == (['old'], ['new'])
        # the page is read once for both versions
        assert len([url for url in index.requests if url.endswith('/demo/')]) == 1
        await provider.aclose()

    @pytest.mark.trio
    async def test_the_name_is_normalized(self, index):
        index.add('Demo_Package', '1.0', metadata='sha256')
        provider = MetadataProvider(client=index.client)

        meta = await provider.metadata('demo-package', '1.0', INDEX_URL)

        assert meta.name == 'demo-package'
        await provider.aclose()
