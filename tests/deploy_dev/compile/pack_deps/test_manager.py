"""
Tests for the maintenance of Update.pack_deps (alasio/deploy_dev/compile/pack_deps/manager.py).

The manager is the tool a mod author runs: it registers the roots of the
closure, resolves every pinned version of the closure against its index, and
replaces the field of the declaration. The tests cover the registration rules
(the lock is the version source), the walk (a dependency outside the lock is
not part of it), and the write (the whole field is replaced, a network failure
leaves the declaration alone).
"""
import os
import sys

import pytest

from alasio.config.entry.mod_entry import ModEntry
from alasio.deploy_dev.compile.pack_deps.manager import PackDepsError, PackDepsManager
from alasio.deploy_dev.compile.pack_deps.resolve import (
    DECISION_PACK, DECISION_PIP, REASON_COMPILED, REASON_FORCED, REASON_INDEX
)
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.compile.pack_deps.conftest import INDEX_URL, UNREACHABLE_INDEX, write_requirements

pytestmark = [
    pytest.mark.parallel,
]

# The build tag of the interpreter that runs the suite: a wheel the target of
# this run can install, whatever python the runner is (the manager resolves for
# the running interpreter, by design)
PY = sys.version_info[:2]
COMPILED_TAG = 'cp{0}{1}-cp{0}{1}-win_amd64'.format(*PY)
COMPILED_WHEEL = f'compiled-3.0-{COMPILED_TAG}.whl'


def manager_of(index, entry=None, **kwargs):
    """
    A manager that reads the mock index.

    The requirements file of the mod names the mock index in the '# from'
    annotation of its pins, so the manager needs no source of its own.

    Args:
        index (MockIndex): The mock index of the test
        entry (ModEntry, optional): The declaration to maintain
        **kwargs: Arguments of PackDepsManager

    Returns:
        PackDepsManager: The manager
    """
    return PackDepsManager(entry=entry, client=index.client, **kwargs)


class TestRegister:
    """The roots of the closure, and the lock they are read from."""

    def test_the_normalized_name(self, mod_root, index):
        write_requirements(mod_root, {'ruamel.yaml': '0.18.6'})
        manager = manager_of(index)

        assert manager.register('Ruamel.YAML') == 'ruamel-yaml'

    def test_an_extra(self, mod_root, index):
        write_requirements(mod_root, {'alasio': '1.0'})
        manager = manager_of(index)

        assert manager.register('alasio[backend,image]') == 'alasio'

    @pytest.mark.parametrize('text', [
        # the version is the pin of the requirements file, never an argument
        'demo==1.0',
        # a url is not a distribution of the lock
        'demo @ https://index.example/simple/demo-1.0-py3-none-any.whl',
        # a marker selects the requirement of another platform
        'demo; python_version < "3.9"',
        # not a requirement at all
        'not a requirement!',
    ])
    def test_refused(self, text, mod_root, index):
        write_requirements(mod_root, {'demo': '1.0'})
        manager = manager_of(index)

        with pytest.raises(PackDepsError):
            manager.register(text)

    def test_a_name_outside_the_lock(self, mod_root, index):
        """The pack channel installs the pinned version only."""
        write_requirements(mod_root, {'demo': '1.0'})
        manager = manager_of(index)

        with pytest.raises(PackDepsError, match='is not pinned'):
            manager.register('other')

    def test_no_requirements_file(self, mod_root, index):
        write_requirements(mod_root, {'demo': '1.0'})
        os.remove(mod_root.joinpath('requirements.txt'))
        manager = manager_of(index)

        with pytest.raises(PackDepsError, match='no requirements file'):
            manager.register('demo')


class TestResolve:
    """The walk of the closure."""

    @pytest.mark.trio
    async def test_the_closure(self, mod_root, index):
        write_requirements(mod_root, {'demo': '1.0', 'pure': '2.0', 'compiled': '3.0'})
        index.add('demo', '1.0', requires=['pure>=2', 'compiled>=3'], metadata='sha256')
        index.add('pure', '2.0', metadata='sha256')
        # a pure wheel and a build of every compiled platform: the pack would
        # replace the build the client installs today
        index.add('compiled', '3.0', metadata='sha256')
        index.add('compiled', '3.0', tag=COMPILED_TAG, metadata='sha256')
        manager = manager_of(index)
        manager.register('demo', recursive=True)

        decisions = await manager.resolve_async()

        assert set(decisions) == {'demo', 'pure', 'compiled'}
        assert decisions['demo'].decision == DECISION_PACK
        assert decisions['pure'].decision == DECISION_PACK
        assert decisions['compiled'].decision == DECISION_PIP
        assert decisions['compiled'].reason == REASON_COMPILED
        assert decisions['compiled'].compiled_wheels == [COMPILED_WHEEL]

    @pytest.mark.trio
    async def test_not_recursive(self, mod_root, index):
        write_requirements(mod_root, {'demo': '1.0', 'pure': '2.0'})
        index.add('demo', '1.0', requires=['pure>=2'], metadata='sha256')
        index.add('pure', '2.0', metadata='sha256')
        manager = manager_of(index)
        manager.register('demo')

        assert set(await manager.resolve_async()) == {'demo'}

    @pytest.mark.trio
    async def test_a_dependency_outside_the_lock(self, mod_root, index):
        """The closure is a subgraph of the requirements file, not of PyPI."""
        write_requirements(mod_root, {'demo': '1.0'})
        index.add('demo', '1.0', requires=['outside>=1'], metadata='sha256')
        manager = manager_of(index)
        manager.register('demo', recursive=True)

        with logger.mock_capture_writer() as capture:
            decisions = await manager.resolve_async()

        assert set(decisions) == {'demo'}
        assert capture.fd.any_contains('are not pinned')
        assert capture.fd.any_contains('outside')

    @pytest.mark.trio
    async def test_the_dependencies_of_an_extra(self, mod_root, index):
        """A registered extra brings its dependencies into the closure."""
        write_requirements(mod_root, {'demo': '1.0', 'http2': '1.0'})
        index.add('demo', '1.0', requires=['http2; extra == "http2"'], metadata='sha256')
        index.add('http2', '1.0', metadata='sha256')

        without = manager_of(index)
        without.register('demo', recursive=True)
        assert set(await without.resolve_async()) == {'demo'}

        with_extra = manager_of(index)
        with_extra.register('demo[http2]', recursive=True)
        assert set(await with_extra.resolve_async()) == {'demo', 'http2'}

    @pytest.mark.trio
    async def test_a_yanked_build_does_not_change_the_decision(self, mod_root, index):
        write_requirements(mod_root, {'demo': '1.0'})
        index.add('demo', '1.0')
        index.add('demo', '1.0', tag=COMPILED_TAG, yanked='broken build')
        manager = manager_of(index)
        manager.register('demo')

        decisions = await manager.resolve_async()

        assert decisions['demo'].decision == DECISION_PACK

    @pytest.mark.trio
    async def test_force(self, mod_root, index):
        """A private distribution is declared without reading an index."""
        write_requirements(mod_root, {'private': '1.0'})
        manager = manager_of(index)
        manager.register('private', force=True)

        decisions = await manager.resolve_async()

        assert decisions['private'].decision == DECISION_PACK
        assert decisions['private'].reason == REASON_FORCED
        assert index.requests == []

    @pytest.mark.trio
    async def test_an_index_that_cannot_be_read(self, mod_root, index):
        """An unknown answer stays on pip, and the report says why."""
        write_requirements(mod_root, {'missing': '1.0'})
        manager = manager_of(index)
        manager.register('missing')

        with logger.mock_capture_writer() as capture:
            decisions = await manager.resolve_async()

        assert decisions['missing'].decision == DECISION_PIP
        assert decisions['missing'].reason == REASON_INDEX
        assert capture.fd.any_contains('Failed to read the index')

    @pytest.mark.trio
    async def test_the_source_of_the_pin(self, mod_root, index, monkeypatch):
        """A '# from' annotation is the index of that pin, whatever else is declared."""
        from alasio.deploy_dev.metadata import provider as provider_module

        write_requirements(mod_root, {'demo': '1.0'})
        index.add('demo', '1.0')
        # the pin names the mock, the default index names another host: only
        # the requirements file is asked
        monkeypatch.setattr(provider_module, 'DEFAULT_INDEX', UNREACHABLE_INDEX)
        manager = PackDepsManager(client=index.client)
        manager.register('demo')

        decisions = await manager.resolve_async()

        assert decisions['demo'].decision == DECISION_PACK
        assert decisions['demo'].source.startswith(INDEX_URL)
        assert all(url.startswith(INDEX_URL) for url in index.requests)

    @pytest.mark.trio
    async def test_a_given_provider_is_reused(self, mod_root, index):
        """A run that already read the pages (a compile) costs the tool no request."""
        from alasio.deploy_dev.metadata import MetadataProvider

        write_requirements(mod_root, {'demo': '1.0', 'pure': '2.0'})
        index.add('demo', '1.0', requires=['pure>=2'], metadata='sha256')
        index.add('pure', '2.0', metadata='sha256')
        provider = MetadataProvider(client=index.client)
        await provider.page('demo', INDEX_URL)
        await provider.page('pure', INDEX_URL)
        before = list(index.requests)

        manager = manager_of(index, provider=provider)
        manager.register('demo', recursive=True)
        decisions = await manager.resolve_async()

        assert decisions['demo'].decision == DECISION_PACK
        new = index.requests[len(before):]
        # the pages are cached, only the METADATA of the two versions is read
        assert len(new) == 2
        assert all(url.endswith('.metadata') for url in new)
        await provider.aclose()

    @pytest.mark.trio
    async def test_the_default_index(self, mod_root, index, monkeypatch):
        """A pin without an annotation is read from PyPI, not from pypi_mirrors.

        The pypi_mirrors of the entry is the source a client installs from and
        may point at the pack server of the mod itself, so it is not the source
        the decision is read from (the rule of the pack server).
        """
        from alasio.deploy_dev.metadata import provider as provider_module

        write_requirements(mod_root, {'demo': '1.0'}, source='')
        index.add('demo', '1.0')
        monkeypatch.setattr(provider_module, 'DEFAULT_INDEX', INDEX_URL)
        # the entry names UNREACHABLE_INDEX in its pypi_mirrors, nothing reaches it
        manager = PackDepsManager(client=index.client)
        manager.register('demo')

        decisions = await manager.resolve_async()

        assert decisions['demo'].decision == DECISION_PACK
        assert all(url.startswith(INDEX_URL) for url in index.requests)

    @pytest.mark.trio
    async def test_no_registered_name(self, mod_root, index):
        manager = manager_of(index)

        with pytest.raises(PackDepsError, match='No distribution is registered'):
            await manager.resolve_async()


class TestWrite:
    """The declaration the run replaces."""

    @pytest.mark.trio
    async def test_the_field_is_replaced(self, mod_root, index):
        write_requirements(mod_root, {'demo': '1.0', 'compiled': '3.0', 'old': '1.0'})
        index.add('demo', '1.0')
        index.add('compiled', '3.0', tag=COMPILED_TAG)
        index.add('old', '1.0')
        # the declaration holds a name the answer does not: it goes away
        entry = ModEntry(mod_root)
        entry.data.Update.pack_deps = ('compiled',)
        entry.write()

        manager = manager_of(index, entry=entry)
        manager.register('demo')
        manager.register('compiled')
        manager.register('old')
        with logger.mock_capture_writer() as capture:
            changed = await manager.write_async()

        assert changed is True
        assert ModEntry(mod_root).data.Update.pack_deps == ('demo', 'old')
        assert capture.fd.any_contains('Removed: compiled')

    @pytest.mark.trio
    async def test_the_second_run_writes_nothing(self, mod_root, index):
        write_requirements(mod_root, {'demo': '1.0'})
        index.add('demo', '1.0')
        manager = manager_of(index)
        manager.register('demo')

        assert await manager.write_async() is True
        assert await manager.write_async() is False

    @pytest.mark.trio
    async def test_an_unknown_index_refuses_the_write(self, mod_root, index):
        """A network failure must not empty the declaration."""
        write_requirements(mod_root, {'demo': '1.0', 'missing': '1.0'})
        index.add('demo', '1.0')
        manager = manager_of(index)
        manager.register('demo')
        manager.register('missing')

        with pytest.raises(PackDepsError, match='nothing is written'):
            await manager.write_async()

        assert ModEntry(mod_root).data.Update.pack_deps == ()

    @pytest.mark.trio
    async def test_a_closure_that_cannot_be_read_refuses_the_write(self, mod_root, index):
        """An unreadable METADATA leaves the closure incomplete: nothing is written."""
        write_requirements(mod_root, {'demo': '1.0', 'pure': '2.0'})
        # no PEP 658 file, and the wheel is not a zip the reader can open
        index.add('demo', '1.0', requires=['pure>=2'], content=b'not a wheel')
        index.add('pure', '2.0')
        manager = manager_of(index)
        manager.register('demo', recursive=True)

        with logger.mock_capture_writer() as capture:
            with pytest.raises(PackDepsError, match='The closure is incomplete'):
                await manager.write_async()

        assert list(manager.errors) == ['demo']
        assert capture.fd.any_contains('Failed to read the dependencies of "demo==1.0"')
        assert capture.fd.any_contains('is incomplete')
        assert ModEntry(mod_root).data.Update.pack_deps == ()

    @pytest.mark.trio
    async def test_the_entry_sees_the_new_value(self, mod_root, index):
        """The cached views of the entry follow the write of the run."""
        write_requirements(mod_root, {'demo': '1.0'})
        index.add('demo', '1.0')
        entry = ModEntry(mod_root)
        manager = manager_of(index, entry=entry)
        manager.register('demo')

        await manager.write_async()

        assert entry.pack_deps == ('demo',)


class TestSyncApi:
    """The facade a script uses, outside a trio run."""

    def test_resolve(self, mod_root, index):
        write_requirements(mod_root, {'demo': '1.0'})
        index.add('demo', '1.0')
        manager = manager_of(index)
        manager.register('demo')

        assert manager.resolve()['demo'].decision == DECISION_PACK
