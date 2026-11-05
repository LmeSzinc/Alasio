"""
Tests of the warmup of a mod.

Two warmups run at backend startup, one thread each:

- the models: every {nav}_model.py of _index/model.index.json, a dependency
  before its user. A model file that inherits a group of another nav guards
  its import with "not in globals()", so the classes of the dependencies are
  put into the namespace before the file runs -- a standalone file has no
  parent package and cannot do the relative import.
- the json: every json the mod serves, read once and kept as msgpack, see
  JSON_PACK_CACHE.

ModPreload is the gate between the warmup and the requests: a request that
arrives before its mod is warm waits on the event of that mod.
"""
import threading

import pytest
import trio

from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.mod_base import ModBase
from alasio.config.entry.mod_entry import ModEntry, ModEntryModel
from alasio.config.entry.model import JSON_PACK_CACHE, MOD_JSON_CACHE
from alasio.config.entry.preload import ModPreload
from alasio.ext.file.loadpy import LOADPY_CACHE
from alasio.ext.path import PathStr
from alasio.testing.filesystem import fs  # noqa: F401

ROOT = '/mods/test_mod'
CONFIG = f'{ROOT}/module/config'

# the in-memory filesystem of the running test, see fake_fs()
FS = None

MAIN_MODEL = """\
class Campaign:
    Name: str = '1'
"""

GEMS_MODEL = """\
import typing as t

if t.TYPE_CHECKING or 'Campaign' not in globals():
    from ..main.main_model import Campaign


class GemsCampaign(Campaign):
    Name: str = '2'
"""

INDEX = {
    'main/main_model.py': {},
    'gems/gems_model.py': {'main/main_model.py': ['Campaign']},
}


def make_mod():
    """
    Returns:
        ModBase: A mod whose root is the fake filesystem
    """
    return ModBase(ModEntry(root=PathStr.new(ROOT), data=ModEntryModel.build(name='test_mod')))


def write(path, content):
    """
    Create a text file in the fake filesystem

    Args:
        path (str): Absolute path
        content (str): File content
    """
    FS.create_file(path, contents=content)


def write_index(data):
    """
    Write _index/model.index.json

    Args:
        data (dict): Content of the index
    """
    import msgspec
    write(f'{CONFIG}/_index/model.index.json', msgspec.json.encode(data).decode('utf-8'))


@pytest.fixture(autouse=True)
def clean_caches():
    """The caches of models and json are global: leave them as found."""
    LOADPY_CACHE.gc()
    JSON_PACK_CACHE.gc()
    MOD_JSON_CACHE.gc(-1)
    yield
    LOADPY_CACHE.gc()
    JSON_PACK_CACHE.gc()
    MOD_JSON_CACHE.gc(-1)


@pytest.fixture(autouse=True)
def fake_fs(fs):
    """
    The in-memory filesystem, exposed to the module helpers
    """
    global FS
    FS = fs
    yield fs
    FS = None


@pytest.fixture
def load_mods(monkeypatch):
    """
    Give the loader the mods the warmup builds its jobs from

    The backend warms MOD_LOADER.dict_mod in its lifespan, and ModPreload
    reads that cached dict: a test replaces it with its own mods.

    Returns:
        callable: Replace the mods for the running test
    """
    def replace(mods):
        monkeypatch.setattr(MOD_LOADER, 'dict_mod', {mod.name: mod for mod in mods}, raising=False)
    return replace


def patched_open_code():
    """
    Route io.open_code to the fake filesystem.

    loadpy reads the source through importlib, which goes through
    io.open_code. The patch is not a fixture: while it is on, an import of any
    module that is not loaded yet (the stdlib itself included) would look in
    the fake filesystem, so it only wraps the load calls of a test.

    Returns:
        ContextManager: The patch
    """
    return FS.patch_open_code()


class TestResolveConfigPath:
    def test_own_file(self):
        """A path of the mod is relative to its config folder."""
        mod = make_mod()
        assert mod.resolve_config_path('gems/gems_config.json') == \
            mod.path_config.joinpath('gems/gems_config.json')

    def test_alasio_file(self):
        """An "alasio/" path points into the config folder of alasio."""
        mod = make_mod()
        assert mod.resolve_config_path('alasio/alasio_i18n.json') == \
            mod.path_alasio_config.joinpath('alasio/alasio_i18n.json')


class TestModelIndex:
    def test_reads_the_generated_index(self):
        """The index is read and decoded on demand, its key order is the load order."""
        write_index(INDEX)
        assert make_mod().model_index_data() == INDEX

    def test_is_not_packed(self):
        """The index is read once to load the models, no request reads it again."""
        write_json_files()
        write_index(INDEX)
        assert make_mod().preload_json() >= 3
        assert JSON_PACK_CACHE.peek(f'{CONFIG}/_index/model.index.json') is None

    def test_missing_index(self):
        """A mod without an index has no dependency, nothing to inject."""
        assert make_mod().model_index_data() == {}

    def test_broken_index(self):
        """A json of the wrong shape is a data error, not a crash."""
        write(f'{CONFIG}/_index/model.index.json', '{"order": ["main/main_model.py"]}')
        assert make_mod().model_index_data() == {}


class TestLoadModel:
    def test_injects_the_class_of_the_parent(self):
        """A cross nav parent is injected, the guarded import never runs."""
        write_index(INDEX)
        write(f'{CONFIG}/main/main_model.py', MAIN_MODEL)
        write(f'{CONFIG}/gems/gems_model.py', GEMS_MODEL)

        mod = make_mod()
        with patched_open_code():
            main = mod.load_model('main/main_model.py')
            gems = mod.load_model('gems/gems_model.py')
        assert gems.GemsCampaign.__bases__[0] is main.Campaign

    def test_without_the_index_the_import_fails(self):
        """The reason the index exists: a bare file cannot import a sibling."""
        write(f'{CONFIG}/main/main_model.py', MAIN_MODEL)
        write(f'{CONFIG}/gems/gems_model.py', GEMS_MODEL)

        mod = make_mod()
        with patched_open_code():
            mod.load_model('main/main_model.py')
            with pytest.raises(ImportError):
                mod.load_model('gems/gems_model.py')

    def test_cycle_in_the_index_is_rejected(self):
        """A cyclic index would recurse forever, it is reported instead."""
        write_index({
            'a/a_model.py': {'b/b_model.py': ['B']},
            'b/b_model.py': {'a/a_model.py': ['A']},
        })
        write(f'{CONFIG}/a/a_model.py', 'class A:\n    pass\n')
        write(f'{CONFIG}/b/b_model.py', 'class B:\n    pass\n')

        with pytest.raises(ImportError) as e:
            make_mod().load_model('a/a_model.py')
        assert 'Circular model import' in str(e.value)

    def test_missing_class_of_the_parent(self):
        """The index names a class the parent does not define."""
        write_index({'a/a_model.py': {'b/b_model.py': ['Nope']}})
        write(f'{CONFIG}/a/a_model.py', 'class A:\n    pass\n')
        write(f'{CONFIG}/b/b_model.py', 'class B:\n    pass\n')

        with patched_open_code():
            with pytest.raises(ImportError) as e:
                make_mod().load_model('a/a_model.py')
        assert 'has no class "Nope"' in str(e.value)

    def test_a_loaded_file_does_not_read_the_index_again(self):
        """Once loaded, a model answers without the file or the index."""
        write_index(INDEX)
        write(f'{CONFIG}/main/main_model.py', MAIN_MODEL)
        write(f'{CONFIG}/gems/gems_model.py', GEMS_MODEL)

        mod = make_mod()
        with patched_open_code():
            gems = mod.load_model('gems/gems_model.py')
        FS.remove(f'{CONFIG}/_index/model.index.json')
        assert mod.load_model('gems/gems_model.py') is gems


class TestPreloadModels:
    def test_loads_every_file_of_the_index(self):
        """A dependency is loaded before the file that inherits it."""
        write_index(INDEX)
        write(f'{CONFIG}/main/main_model.py', MAIN_MODEL)
        write(f'{CONFIG}/gems/gems_model.py', GEMS_MODEL)

        mod = make_mod()
        with patched_open_code():
            assert mod.preload_models() == 2
        assert LOADPY_CACHE.get(f'{CONFIG}/main/main_model.py').Campaign
        assert LOADPY_CACHE.get(f'{CONFIG}/gems/gems_model.py').GemsCampaign

    def test_a_broken_file_does_not_stop_the_rest(self):
        """One unloadable model must not leave the whole mod cold."""
        write_index({
            'main/main_model.py': {},
            'bad/bad_model.py': {},
            'gems/gems_model.py': {'main/main_model.py': ['Campaign']},
        })
        write(f'{CONFIG}/main/main_model.py', MAIN_MODEL)
        write(f'{CONFIG}/gems/gems_model.py', GEMS_MODEL)
        write(f'{CONFIG}/bad/bad_model.py', 'class Bad(:\n')

        mod = make_mod()
        with patched_open_code():
            assert mod.preload_models() == 3
        assert LOADPY_CACHE.get(f'{CONFIG}/gems/gems_model.py').GemsCampaign


CONFIG_INDEX = {
    'main': {
        'file': 'main/main_config.json',
        'i18n': ['alasio/alasio_i18n.json', 'main/main_i18n.json'],
        'config': {'task': ['Main'], 'group': []},
    },
}


def write_json_files():
    """Create the config index and the json it names."""
    import msgspec
    write(f'{CONFIG}/_index/config.index.json', msgspec.json.encode(CONFIG_INDEX).decode('utf-8'))
    write(f'{CONFIG}/main/main_config.json', '{"Group": {"arg": {"group": "Group", "arg": "arg"}}}')
    write(f'{CONFIG}/main/main_i18n.json', '{"Group": {"arg": {"name": {"en-US": "Arg"}}}}')


class TestPreloadJson:
    def test_packs_the_config_and_i18n_of_every_nav(self):
        """The files of config.index.json are read once and kept as msgpack."""
        write_json_files()
        mod = make_mod()

        # main_config.json, main_i18n.json; the alasio i18n is not in this fake fs
        assert mod.preload_json() >= 3
        pack = JSON_PACK_CACHE.get(f'{CONFIG}/main/main_config.json')
        assert isinstance(pack, bytes)
        assert MOD_JSON_CACHE.get(
            f'{CONFIG}/main/main_config.json').keys() == {'Group'}

    def test_a_request_reads_the_pack_not_the_file(self):
        """After the warmup the file is not needed any more."""
        write_json_files()
        mod = make_mod()
        mod.preload_json()

        path = f'{CONFIG}/main/main_i18n.json'
        FS.remove(path)
        MOD_JSON_CACHE.gc(-1)
        # the TTL cache was released, the pack answers the second decode
        assert MOD_JSON_CACHE.get(path) == {'Group': {'arg': {'name': {'en-US': 'Arg'}}}}

    def test_missing_files_are_skipped(self):
        """A mod may ship without an index, the warmup is silent about it."""
        assert make_mod().preload_json() == 0


class FakeMod:
    """A mod of two phases, the model phase waits on a gate."""

    name = 'fake'

    def __init__(self):
        self.gate = threading.Event()
        self.started = threading.Event()

    def preload_models(self):
        self.started.set()
        self.gate.wait(2)
        return 1

    def preload_json(self):
        return 2


class BrokenMod:
    """A mod whose model phase fails."""

    name = 'broken'

    def preload_models(self):
        raise ValueError('boom')

    def preload_json(self):
        return 0


@pytest.mark.trio
class TestModPreload:
    async def test_wait_blocks_until_both_phases_end(self, load_mods):
        """The gate opens when the models and the json are both loaded."""
        preload = ModPreload()
        mod = FakeMod()
        load_mods([mod])
        async with trio.open_nursery() as nursery:
            nursery.start_soon(preload.run)
            await trio.to_thread.run_sync(mod.started.wait)
            with trio.move_on_after(0.05) as scope:
                await preload.wait(mod.name)
            assert scope.cancelled_caught, 'the model phase is still blocked'
            mod.gate.set()
            # now the phase ends and the gate opens
            await preload.wait(mod.name)
            nursery.cancel_scope.cancel()

    async def test_jobs_come_from_the_loader(self, load_mods):
        """The warmup and the gate share one job per mod, built once."""
        mod = FakeMod()
        load_mods([mod])
        preload = ModPreload()
        assert preload.jobs[mod.name].mod is mod
        # cached: a request builds no second job, hence waits on the warmup
        assert preload.jobs[mod.name] is preload.jobs[mod.name]

    async def test_wait_of_an_unknown_mod_returns(self, load_mods):
        """No such mod, or a process that does not warm up: no wait."""
        load_mods([])
        preload = ModPreload()
        await preload.wait('')
        await preload.wait('unknown')

    async def test_failure_opens_the_gate(self, load_mods):
        """A broken mod must not lock its requests out."""
        preload = ModPreload()
        load_mods([BrokenMod()])
        await preload.run()
        await preload.wait('broken')
