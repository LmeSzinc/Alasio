"""
Tests for ProbeBase: the concurrent candidate probe engine, and for
populate_mirrors(): the normalization of the three mirror input forms.

The engine is transport agnostic, the tests drive it with a stub
subclass of canned results and errors, no http is involved.
"""
import threading

import pytest

from alasio.deploy.httpclient.probe import AllMirrorsFailedError, Mirrors, ProbeBase, populate_mirrors
from alasio.logger import logger


class TestPopulateMirrors:
    """The three input forms, normalized into the full structure."""

    def test_str_is_a_single_mirror(self):
        """A str is one mirror named 'default'."""
        assert populate_mirrors('http://only/') == {
            'default': {
                'default': 'http://only',
            },
        }

    def test_flat_dict_entries_are_their_own_group(self):
        """In the flat form the key is the mirror name of a group of
        its own."""
        assert populate_mirrors({
            'cn': 'http://cn',
            'global': 'http://global',
        }) == {
            'cn': {
                'cn': 'http://cn',
            },
            'global': {
                'global': 'http://global',
            },
        }

    def test_full_structure_is_kept(self):
        """The full structure passes through, the order is kept."""
        mirrors = {
            'cn': {
                '123pan': 'http://pan',
                'tencent-cos': 'http://cos',
            },
            'global': {
                'global': 'http://global',
            },
        }
        populated = populate_mirrors(mirrors)
        assert populated == mirrors
        assert list(populated) == ['cn', 'global']
        assert list(populated['cn']) == ['123pan', 'tencent-cos']

    def test_mixed_forms(self):
        """The flat and the nested form may mix per group."""
        assert populate_mirrors({
            'cn': {
                '123pan': 'http://pan',
            },
            'global': 'http://global',
        }) == {
            'cn': {
                '123pan': 'http://pan',
            },
            'global': {
                'global': 'http://global',
            },
        }

    @pytest.mark.parametrize('mirrors, error', [
        ({}, ValueError),
        ({'cn': {}}, ValueError),
        ([], TypeError),
        ({'cn': 123}, TypeError),
        ({1: {'a': 'http://a'}}, TypeError),
    ])
    def test_invalid_input(self, mirrors, error):
        """An empty input or a malformed structure is rejected."""
        with pytest.raises(error):
            populate_mirrors(mirrors)

    @pytest.mark.parametrize('name', ['', 'a/b', 'a b', '..', 'x' * 65])
    def test_invalid_name(self, name):
        """A malformed mirror name is rejected."""
        with pytest.raises(ValueError):
            populate_mirrors({
                'cn': {
                    name: 'http://pan',
                },
            })

    def test_duplicate_name_across_groups(self):
        """A mirror name must be unique across the groups."""
        with pytest.raises(ValueError, match='Duplicate mirror name'):
            populate_mirrors({
                'cn': {
                    '123pan': 'http://a',
                },
                'global': {
                    '123pan': 'http://b',
                },
            })

    @pytest.mark.parametrize('url', [
        '', 'ftp://h', 'http://', 'http:///', 'h', 'http:/h', 'https://', '://x',
    ])
    def test_invalid_url(self, url):
        """A url that is not an http/https url is rejected."""
        with pytest.raises(ValueError):
            populate_mirrors({
                'cn': {
                    'a': url,
                },
            })


class TestMirrors:
    """Mirrors.from_input() and the shared lookups."""

    def test_from_input(self):
        """The three forms are accepted, a Mirrors is returned as-is."""
        assert Mirrors.from_input('http://only/').groups == {'default': {'default': 'http://only'}}
        mirrors = Mirrors.from_input({'cn': 'http://cn', 'global': 'http://global'})
        assert Mirrors.from_input(mirrors) is mirrors
        assert mirrors.url_of('cn') == 'http://cn'
        assert mirrors.url_of('nope') == ''

    def test_single(self):
        """A single mirror name is resolved, a multi one is not."""
        assert Mirrors.from_input('http://only').single == 'default'
        assert Mirrors.from_input({'cn': {'a': 'http://a'}}).single == 'a'
        assert Mirrors.from_input({'cn': 'http://cn', 'global': 'http://global'}).single == ''


class StubProbe(ProbeBase):
    """
    A probe of canned results: probe_function() returns the result
    registered for a name, or raises the registered exception.
    """

    def __init__(self, mirrors, results):
        """
        Args:
            mirrors (dict): The mirror structure
            results (dict[str, Any]): {name: result | Exception}
        """
        super().__init__(mirrors)
        self.results = results
        # (name, url) of every probe_function call, in call order
        self.calls = []

    def probe_function(self, name, url):
        self.calls.append((name, url))
        result = self.results[name]
        if isinstance(result, Exception):
            raise result
        return result


class TestProbeBase:
    """The race: groups run in parallel, members fall back in order."""

    def test_member_order(self):
        """The members of a group are probed in the declared order, a
        failed member falls through to the next one."""
        probe = StubProbe(
            {'cn': {'down': 'http://down', 'up': 'http://up'}},
            {'down': ValueError('boom'), 'up': 'UP'})
        with logger.mock_capture_writer():
            name, result = probe.run()
        assert (name, result) == ('up', 'UP')
        assert probe.calls == [('down', 'http://down'), ('up', 'http://up')]

    def test_preferred_member_wins(self):
        """A usable preferred member is not bypassed by the fallback."""
        probe = StubProbe(
            {'cn': {'preferred': 'http://a', 'fallback': 'http://b'}},
            {'preferred': 'P', 'fallback': 'F'})
        with logger.mock_capture_writer():
            name, result = probe.run()
        assert (name, result) == ('preferred', 'P')
        assert probe.calls == [('preferred', 'http://a')]

    def test_groups_race(self, monkeypatch):
        """Groups run in parallel, the fastest one wins, a slow group
        is not awaited."""
        release = threading.Event()
        entered = threading.Event()
        worker_done = {'slow': threading.Event(), 'fast': threading.Event()}

        class SlowProbe(StubProbe):
            def probe_function(self, name, url):
                if name == 'slow':
                    entered.set()
                    # the losing group is abandoned by the probe, it
                    # ends when the test releases it
                    release.wait(timeout=3)
                return super().probe_function(name, url)

        probe = SlowProbe(
            {'slow': {'slow': 'http://slow'}, 'fast': {'fast': 'http://fast'}},
            {'slow': 'S', 'fast': 'F'})
        # observe the completion of the group workers: the abandoned
        # worker logs its result after the probe returned, the test
        # waits for it before leaving the capture
        original_probe_group = probe._probe_group

        def probe_group(members, results):
            try:
                original_probe_group(members, results)
            finally:
                worker_done[next(iter(members))].set()

        monkeypatch.setattr(probe, '_probe_group', probe_group)
        with logger.mock_capture_writer():
            try:
                name, result = probe.run()
                assert (name, result) == ('fast', 'F')
                assert entered.wait(3)
                # the probe returned while the slow group is still running
                assert not worker_done['slow'].is_set()
            finally:
                release.set()
            assert worker_done['slow'].wait(3)

    def test_all_failed(self):
        """Every candidate fails: the error carries the reasons."""
        probe = StubProbe(
            {'a': {'a': 'http://a'}, 'b': {'b': 'http://b'}},
            {'a': ValueError('down'), 'b': KeyError('gone')})
        with logger.mock_capture_writer() as capture:
            with pytest.raises(AllMirrorsFailedError) as e:
                probe.run()
        message = str(e.value)
        assert 'a: ValueError: down' in message
        assert "b: KeyError: 'gone'" in message
        assert capture.backend.any_contains('is not usable')

    def test_mirrors_input(self):
        """A Mirrors instance is accepted as the probe input."""
        mirrors = Mirrors.from_input({'g': {'a': 'http://a'}})
        probe = StubProbe(mirrors, {'a': 'A'})
        with logger.mock_capture_writer():
            name, result = probe.run()
        assert (name, result) == ('a', 'A')

    def test_result_is_handed_over(self):
        """The result of the winner is returned as-is."""
        token = object()
        probe = StubProbe({'g': {'a': 'http://a'}}, {'a': token})
        with logger.mock_capture_writer():
            name, result = probe.run()
        assert name == 'a'
        assert result is token

    def test_probe_function_not_implemented(self):
        """The base probe_function makes every candidate unusable."""
        probe = ProbeBase({'g': {'a': 'http://a'}})
        with logger.mock_capture_writer():
            with pytest.raises(AllMirrorsFailedError) as e:
                probe.run()
        assert 'a: NotImplementedError' in str(e.value)
