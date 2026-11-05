"""
Tests for the mod entry (alasio/config/entry/mod_entry.py).

The entry of a mod is `module/config/_index/mod_entry.yaml`, a data file: the
reader parses it and never imports the mod. The tests cover the yaml model,
the strict validation (a declaration is a fixed value, it is never silently
replaced by a default), the views the runtime reads (ordered sets and tuples),
the location rules, the one walk up that locates a mod root, and the writer
that keeps the file in the shape of the model.
"""
import pytest

from alasio.base.filter import parse_filter
from alasio.config.entry.mod_entry import (
    ALASIO_CONFIG, DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS, MOD_CONFIG, MOD_ENTRY_FILE, MOD_ENTRY_FOLDER, ModEntry,
    ModEntryError, ModEntryModel
)
from alasio.config_dev.format.format_i18n import format_i18n
from alasio.deploy.httpclient.probe_mirrors import Mirrors
from alasio.ext import env
from alasio.ext.cache.msgspec_meta import get_field_metadata
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

# A complete declaration, every key the model accepts
FULL_ENTRY = """\
Mod:
  name: mod_a
  path_main: module/entry.py
  path_assets: res
  asset_ext: [.png, .webp]
  asset_lang: [cn, jp]
  gui_language: [zh-CN, ja-JP]
Scheduler:
  task_entry_folders: [module, jobs]
  scheduler_priority: |
    RestartDevice > RestartGame
Update:
  mirrors: https://mirror.example/alas
  pypi_mirrors:
    cn:
      self: https://pypi.example/simple
  provide_wheel: [https://a.example/simple/]
  pack_deps: [httpx, ruamel.yaml]
  requirements_file: deps/requirements.txt
"""

# The priority of the example mod as the python declaration of the mod wrote
# it: the yaml declaration has to carry the same order, the migration moves
# the value, it does not edit it
EXAMPLE_PRIORITY_PYTHON = """
    RestartDevice > RestartGame
    > OpsiCrossMonth
    > Commission > Tactical > Research
    > Exercise
    > Dorm > Meowfficer > Guild > Gacha
    > Reward
    > ShopFrequent > ShopOnce > Shipyard > Freebies
    > PrivateQuarters
    > OpsiExplore
    > Minigame > Awaken
    > OpsiAshBeacon
    > OpsiDaily > OpsiShop > OpsiVoucher
    > OpsiAbyssal > OpsiStronghold > OpsiObscure > OpsiArchive
    > Daily > Hard > OpsiAshBeacon > OpsiAshAssist > OpsiMonthBoss
    > Sos > EventSp > EventA > EventB > EventC > EventD
    > RaidDaily > CoalitionSp > WarArchives > MaritimeEscort
    > Event > Event2 > Raid > Hospital > Coalition > Main > Main2 > Main3
    > OpsiMeowfficerFarming
    > GemsFarming
    > OpsiHazard1Leveling
    """


def write_entry(fs, root, source):
    """
    Write an entry declaration under a mod root of the fake filesystem.

    Args:
        fs (FakeFilesystem): Fake filesystem of the test
        root (str): Root folder of the mod
        source (str): Content of the declaration

    Returns:
        str: Path of the written file
    """
    file = f'{root}/{MOD_CONFIG}/{MOD_ENTRY_FOLDER}/{MOD_ENTRY_FILE}'
    fs.create_file(file, contents=source)
    return file


class TestLocation:
    """The path of the declaration, the one layout a mod has."""

    def test_file(self):
        """The file lives in the config folder of the mod, in the _index folder."""
        assert ModEntry('E:/mod').file == f'E:/mod/{MOD_CONFIG}/{MOD_ENTRY_FOLDER}/{MOD_ENTRY_FILE}'
        assert ModEntry('E:/mod/').file == f'E:/mod/{MOD_CONFIG}/{MOD_ENTRY_FOLDER}/{MOD_ENTRY_FILE}'

    def test_the_layout_is_a_constant(self):
        """There is no parameter to change the layout: a mod is module/config."""
        assert MOD_CONFIG == 'module/config'
        assert ALASIO_CONFIG == 'alasio/config'

    def test_unknown_layout(self):
        """A layout no reader looks for is refused at construction."""
        with pytest.raises(ValueError, match='path_config must be'):
            ModEntry(path_config='module/configs')

    def test_root_is_absolute(self, fs):
        """A relative root is resolved against the working folder, once."""
        fs.create_dir('/work/mod')
        fs.chdir('/work')

        root = PathStr.new(ModEntry('mod').root)

        assert root.name == 'mod'
        assert root.isdir()

    def test_empty_root(self):
        """An entry without a location keeps an empty root."""
        assert ModEntry().root == ''


class TestFromSource:
    """Parse the yaml text into a mod entry, without a filesystem."""

    def test_full(self):
        """Every key of the declaration reaches the model."""
        model = ModEntry.from_source(FULL_ENTRY).data

        assert model.Mod.name == 'mod_a'
        assert model.Mod.path_main == 'module/entry.py'
        assert model.Mod.path_assets == 'res'
        assert model.Mod.asset_ext == ['.png', '.webp']
        assert model.Mod.asset_lang == ['cn', 'jp']
        assert model.Mod.gui_language == ['zh-CN', 'ja-JP']
        assert model.Scheduler.task_entry_folders == ['module', 'jobs']
        assert model.Update.mirrors == 'https://mirror.example/alas'
        assert model.Update.pypi_mirrors == {'cn': {'self': 'https://pypi.example/simple'}}
        assert model.Update.provide_wheel == ('https://a.example/simple/',)
        assert model.Update.requirements_file == 'deps/requirements.txt'
        assert model.Update.pack_deps == ('httpx', 'ruamel.yaml')
        assert tuple(parse_filter(model.Scheduler.scheduler_priority)) == ('RestartDevice', 'RestartGame')

    def test_no_root(self):
        """A source has no location: the entry carries the declaration only."""
        assert ModEntry.from_source(FULL_ENTRY).root == ''

    def test_defaults(self):
        """A declaration of the name only keeps every default of the model."""
        assert ModEntry.from_source('Mod:\n  name: m\n').data == ModEntryModel.build(name='m')

    def test_name_is_required(self):
        """A declaration without a name names no mod."""
        with pytest.raises(ModEntryError, match='name is required'):
            ModEntry.from_source('Mod:\n  path_main: module/entry.py\n')

    def test_empty_file(self):
        """An empty declaration has no name either."""
        with pytest.raises(ModEntryError, match='name is required'):
            ModEntry.from_source('')

    def test_not_a_mapping(self):
        """The entry is a mapping of keys, a list of values is not."""
        with pytest.raises(ModEntryError, match='the entry must be a mapping'):
            ModEntry.from_source('- name\n- root\n')

    def test_invalid_yaml(self):
        """A broken document is reported as a yaml error."""
        with pytest.raises(ModEntryError, match='invalid yaml'):
            ModEntry.from_source('Mod:\n  name: [unclosed\n')

    def test_wrong_type(self):
        """A value of the wrong type is refused instead of coerced."""
        with pytest.raises(ModEntryError, match='invalid entry value'):
            ModEntry.from_source('Mod:\n  name: 123\n')

    def test_wrong_type_of_a_list(self):
        """The collections are lists in the file, a string is not one."""
        with pytest.raises(ModEntryError, match='invalid entry value'):
            ModEntry.from_source('Mod:\n  name: m\n  asset_lang: cn\n')

    def test_unknown_key_is_ignored(self):
        """A key the model does not know is ignored, msgspec drops it."""
        assert ModEntry.from_source('Mod:\n  name: m\nfuture_key: 1\n').name == 'm'

    @pytest.mark.parametrize('url', ['ftp://a.example/simple', 'a.example/simple', 'https://'])
    def test_provided_index_must_be_http(self, url):
        """An index the pack server cannot request is refused, not silently skipped."""
        with pytest.raises(ModEntryError, match='invalid entry value'):
            ModEntry.from_source(f'Mod:\n  name: m\nUpdate:\n  provide_wheel: ["{url}"]\n')

    @pytest.mark.parametrize('key', ['root', 'path_config'])
    def test_location_keys_are_ignored(self, key):
        """The location belongs to ModEntry, a file that declares it is ignored."""
        entry = ModEntry.from_source(f'Mod:\n  name: m\n{key}: somewhere\n')

        assert entry.name == 'm'
        assert entry.root == ''
        assert entry.path_config == MOD_CONFIG

    def test_source_name_in_the_error(self):
        """The caller may name the source, e.g. a commit of a repo."""
        with pytest.raises(ModEntryError, match=r'mod_entry\.yaml at commit c3: invalid entry value'):
            ModEntry.from_source('Mod:\n  name: 123\n', source_name='mod_entry.yaml at commit c3')

    def test_scheduler_priority_dsl(self):
        """The priority is the DSL of alasio.base.filter, comment lines included."""
        entry = ModEntry.from_source(
            'Mod:\n  name: m\n'
            'Scheduler:\n  scheduler_priority: |\n'
            '    RestartDevice > RestartGame\n'
            '    # a comment\n'
            '    > Commission > Exercise\n'
        )

        assert tuple(parse_filter(entry.scheduler_priority)) == (
            'RestartDevice', 'RestartGame', 'Commission', 'Exercise')

    def test_scheduler_priority_edges_are_normalized(self):
        """A block scalar and a plain scalar declare the same order."""
        block = ModEntry.from_source('Mod:\n  name: m\nScheduler:\n  scheduler_priority: |\n    A > B\n')
        plain = ModEntry.from_source('Mod:\n  name: m\nScheduler:\n  scheduler_priority: A > B\n')
        indented = ModEntry.from_source('Mod:\n  name: m\nScheduler:\n  scheduler_priority: "  A > B  "\n')

        assert block.scheduler_priority == 'A > B'
        assert block.scheduler_priority == plain.scheduler_priority == indented.scheduler_priority

    @pytest.mark.parametrize('declared, expected', [
        # a single url: only one candidate
        ('  mirrors: https://only.example/simple\n',
         {'default': 'https://only.example/simple'}),
        # name to url: each mirror is a group of its own
        ('  mirrors:\n    cn: https://cn.example/simple\n    global: https://global.example/simple\n',
         {'cn': 'https://cn.example/simple', 'global': 'https://global.example/simple'}),
        # group to mirrors: the members of a group are tried in order
        ('  mirrors:\n    cn:\n      self: https://self.example/simple\n'
         '      other: https://other.example/simple\n',
         {'self': 'https://self.example/simple', 'other': 'https://other.example/simple'}),
        # the flat and the nested form mixed per group
        ('  mirrors:\n    cn:\n      self: https://self.example/simple\n'
         '    global: https://global.example/simple\n',
         {'self': 'https://self.example/simple', 'global': 'https://global.example/simple'}),
    ])
    def test_mirror_forms(self, declared, expected):
        """The mirrors keep the input forms of Mirrors.from_input()."""
        entry = ModEntry.from_source('Mod:\n  name: m\nUpdate:\n' + declared)

        assert Mirrors.from_input(entry.mirrors).urls == expected


class TestViews:
    """The fields in the shape the runtime reads, the repeats dropped."""

    def test_ordered_sets(self):
        """The declared languages are dicts, a repeat is kept at its first place."""
        entry = ModEntry.from_source(
            'Mod:\n  name: m\n  asset_lang: [cn, en, cn]\n  gui_language: [zh-CN]\n'
        )

        assert entry.asset_lang == {'cn': None, 'en': None}
        assert entry.gui_language == {'zh-CN': None}
        # the model keeps the file as it is written
        assert entry.data.Mod.asset_lang == ['cn', 'en', 'cn']

    def test_ordered_tuples(self):
        """The declared lists are tuples, a repeat is kept once."""
        entry = ModEntry.from_source(
            'Mod:\n  name: m\nUpdate:\n'
            '  pack_deps: [httpx, ruamel.yaml, httpx]\n'
            '  provide_wheel: [https://a.example/simple/, https://a.example/simple/]\n'
        )

        assert entry.pack_deps == ('httpx', 'ruamel.yaml')
        assert entry.provide_wheel == ('https://a.example/simple/',)

    def test_empty_lists(self):
        """An empty list is an empty collection, not the default."""
        entry = ModEntry.from_source('Mod:\n  name: m\nUpdate:\n  pack_deps: []\n  provide_wheel: []\n')

        assert entry.pack_deps == ()
        assert entry.provide_wheel == ()

    def test_scalars(self):
        """The scalar fields are the model values."""
        entry = ModEntry.from_source(FULL_ENTRY)

        assert entry.name == 'mod_a'
        assert entry.path_main == 'module/entry.py'
        assert entry.path_assets == 'res'
        assert entry.requirements_file == 'deps/requirements.txt'
        # the mirrors are parsed once, the callers read the structure
        assert entry.mirrors.urls == {'default': 'https://mirror.example/alas'}

    def test_iter_asset_lang(self):
        """The shared assets are tried first, then the declared languages."""
        entry = ModEntry.from_source('Mod:\n  name: m\n  asset_lang: [cn, jp]\n')

        assert list(entry.iter_asset_lang()) == ['', 'cn', 'jp']

    def test_defaults(self):
        """The views of a declaration of the name only are the defaults."""
        entry = ModEntry.from_source('Mod:\n  name: m\n')

        assert entry.path_main == 'module/main.py'
        assert entry.asset_ext == {'.png': None, '.gif': None}
        assert entry.provide_wheel == DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS
        assert entry.scheduler_priority == 'RestartDevice > RestartGame'
        # no update source declared, the mod is unmanaged
        assert entry.mirrors is None
        assert entry.pypi_mirrors.urls == {
            'tsinghua': 'https://pypi.tuna.tsinghua.edu.cn/simple',
            'aliyun': 'https://mirrors.aliyun.com/pypi/simple',
            'tencent': 'https://mirrors.cloud.tencent.com/pypi/simple',
            'pypi': 'https://pypi.org/simple',
        }

    def test_pypi_mirrors_are_a_copy(self):
        """The default pypi mirrors of two entries never share one structure."""
        first = ModEntry.from_source('Mod:\n  name: a\n')
        second = ModEntry.from_source('Mod:\n  name: b\n')

        first.data.Update.pypi_mirrors['cn'].pop('tsinghua')

        assert 'tsinghua' in second.data.Update.pypi_mirrors['cn']
        assert 'tsinghua' in ModEntry.from_source('Mod:\n  name: c\n').data.Update.pypi_mirrors['cn']


class TestData:
    """The declaration an entry answers."""

    def test_given_data(self):
        """A caller that holds a declaration does not touch the filesystem."""
        model = ModEntryModel.build(name='m')

        assert ModEntry(data=model).data is model

    def test_read_from_the_location(self, fs):
        """The declaration is read from the config folder of the root."""
        write_entry(fs, '/mod', 'Mod:\n  name: m\n')

        assert ModEntry('/mod').data.Mod.name == 'm'

    def test_data_is_cached(self, fs):
        """The file is read once, a later edit is not seen."""
        file = write_entry(fs, '/mod', 'Mod:\n  name: m\n')
        entry = ModEntry('/mod')
        assert entry.data.Mod.name == 'm'

        with open(file, 'w', encoding='utf-8') as f:
            f.write('name: changed\n')

        assert entry.data.Mod.name == 'm'

    def test_missing_declaration(self, fs):
        """A folder that declares no entry answers no data."""
        fs.create_dir('/mod')

        with pytest.raises(FileNotFoundError):
            _ = ModEntry('/mod').data

    def test_error_names_the_file(self, fs):
        """The error of a broken declaration names the file to fix."""
        file = write_entry(fs, '/mod', 'Mod:\n  name: 1\n')

        with pytest.raises(ModEntryError, match=file):
            _ = ModEntry('/mod').data


class TestIsMod:
    """A folder is a mod root when it declares an entry, nothing else."""

    def test_entry(self, fs):
        """The declaration makes the folder a mod."""
        write_entry(fs, '/mod', 'Mod:\n  name: m\n')

        assert ModEntry.is_mod('/mod') is True

    @pytest.mark.parametrize('marker', [
        # a generated index: an output of a mod, not what makes it one
        'module/config/_index/config.index.json',
        # the scheduler and the code folder of a mod
        'module/main.py',
        # the assets of a mod
        'assets/cn/a.png',
        # the old declaration, removed by the migration
        'module/config/const.py',
    ])
    def test_not_a_mod(self, fs, marker):
        """A partial mod layout declares no entry, so it is not a mod."""
        fs.create_file(f'/mod/{marker}', contents='')

        assert ModEntry.is_mod('/mod') is False

    def test_missing_folder(self, fs):
        """A folder that does not exist is not a mod."""
        assert ModEntry.is_mod('/missing') is False


class TestCreate:
    """The one way the code of a mod locates its root: it states its own depth."""

    def test_from_the_root(self, fs):
        """A folder at the depth 0 is the root itself."""
        write_entry(fs, '/mod', 'Mod:\n  name: m\n')

        assert ModEntry.create('/mod', uppath=0).root == PathStr.new('/mod')

    def test_from_a_folder(self, fs):
        """A folder resolves to the root the stated levels above it."""
        write_entry(fs, '/mod', 'Mod:\n  name: m\n')

        assert ModEntry.create('/mod/module', uppath=1).root == PathStr.new('/mod')
        assert ModEntry.create('/mod/module/config', uppath=2).root == PathStr.new('/mod')

    def test_from_a_file(self, fs):
        """A file resolves to the root, the usual call is from __file__."""
        write_entry(fs, '/mod', 'Mod:\n  name: m\n')
        fs.create_file('/mod/module/map/map.py', contents='')

        assert ModEntry.create('/mod/module/map/map.py', uppath=3).root == PathStr.new('/mod')

    def test_the_declaration_is_read(self, fs):
        """The entry answers the declaration of the root it computed."""
        write_entry(fs, '/mod', 'Mod:\n  name: m\n')
        fs.create_file('/mod/module/map/map.py', contents='')

        assert ModEntry.create('/mod/module/map/map.py', uppath=3).data.Mod.name == 'm'

    def test_no_walk_up(self, fs):
        """A depth that lands between the root and the file is not searched up."""
        write_entry(fs, '/mod', 'Mod:\n  name: m\n')
        fs.create_file('/mod/module/map/map.py', contents='')

        entry = ModEntry.create('/mod/module/map/map.py', uppath=1)

        assert entry.root == PathStr.new('/mod/module/map')
        with pytest.raises(FileNotFoundError):
            _ = entry.data

    def test_nested_mod(self, fs):
        """A nested mod is its own mod, the root is the one stated, not the nearest."""
        write_entry(fs, '/mod', 'Mod:\n  name: outer\n')
        write_entry(fs, '/mod/sub', 'Mod:\n  name: inner\n')

        assert ModEntry.create('/mod/sub/module/config', uppath=2).data.Mod.name == 'inner'
        assert ModEntry.create('/mod/module/config', uppath=2).data.Mod.name == 'outer'

    def test_no_mod(self, fs):
        """A folder without a declaration creates an entry that has nothing to read."""
        fs.create_file('/work/module/main.py', contents='')

        entry = ModEntry.create('/work/module/main.py', uppath=2)

        assert entry.root == PathStr.new('/work')
        with pytest.raises(FileNotFoundError):
            _ = entry.data

    @pytest.mark.parametrize('file', [
        # another layout: a mod is module/config, not any folder holding a file
        'modules/config/_index/mod_entry.yaml',
        'module/configs/_index/mod_entry.yaml',
        # another file name
        'module/config/_index/mod_entry.yml',
        # the declaration of the library, which is not a mod
        'alasio/config/_index/mod_entry.yaml',
    ])
    def test_only_the_mod_layout(self, fs, file):
        """Only the fixed relative path of a mod is read."""
        fs.create_file(f'/work/{file}', contents='Mod:\n  name: m\n')

        with pytest.raises(FileNotFoundError):
            _ = ModEntry.create('/work', uppath=0).data

    def test_relative_path(self, fs):
        """A relative path is resolved against the working folder."""
        write_entry(fs, '/mod', 'Mod:\n  name: m\n')
        fs.chdir('/mod')

        root = ModEntry.create('module/config', uppath=2).root

        assert PathStr.new(root).name == 'mod'
        assert ModEntry(root).data.Mod.name == 'm'


class TestValidate:
    """The check a caller makes on an entry it did not create at a known path."""

    def test_valid(self, fs):
        """A readable declaration validates, and is cached by the check."""
        write_entry(fs, '/mod', 'Mod:\n  name: m\n')
        entry = ModEntry('/mod')

        assert entry.validate() is True
        assert entry.name == 'm'

    def test_missing_file(self, fs):
        """A folder that declares nothing is not valid, and is silent."""
        with logger.mock_capture_writer() as capture:
            assert ModEntry('/mod').validate() is False
        assert capture.concat() == ''

    def test_invalid_declaration(self, fs):
        """A broken declaration is not valid, and the reason is reported."""
        write_entry(fs, '/mod', 'Mod:\n  name: [unclosed\n')
        entry = ModEntry('/mod')

        with logger.mock_capture_writer() as capture:
            assert entry.validate() is False
        assert 'invalid yaml' in capture.concat()

    def test_a_name_is_required(self, fs):
        """A declaration that names no mod is not valid."""
        write_entry(fs, '/mod', 'Mod:\n  path_main: module/entry.py\n')

        with logger.mock_capture_writer() as capture:
            assert ModEntry('/mod').validate() is False
        assert 'name is required' in capture.concat()


class TestWrite:
    """The declaration is maintained from the model."""

    def test_writes_every_key_of_the_model(self, fs):
        """A mod without a declaration gets one, with every key of the model."""
        entry = ModEntry('/mod', data=ModEntryModel.build(name='test'))

        assert entry.write() is True

        text = open(entry.file, encoding='utf-8').read()
        keys = [line.partition(':')[0] for line in text.splitlines()
                if line and not line.startswith(('#', '-', ' '))]
        assert keys == list(ModEntryModel.__struct_fields__)

    def test_comments_come_from_the_model(self, fs):
        """The help annotation of the model is the comment of the key."""
        entry = ModEntry('/mod', data=ModEntryModel.build(name='test'))

        entry.write()

        text = open(entry.file, encoding='utf-8').read()
        assert '# Name of the mod, the key the framework identifies it by' in text
        assert '# Task order of the scheduler, a task runs before the tasks after it' in text

    def test_read_back_is_the_entry(self, fs):
        """The written declaration reads back to the value it was written from."""
        entry = ModEntry('/mod', data=ModEntry.from_source(FULL_ENTRY).data)

        entry.write()

        assert ModEntry('/mod').data == entry.data

    def test_second_write_touches_nothing(self, fs):
        """The write is skipped when the file already holds the content."""
        entry = ModEntry('/mod', data=ModEntryModel.build(name='test'))
        assert entry.write() is True

        assert entry.write() is False

    def test_missing_key_is_added_with_the_default(self, fs):
        """A declaration of some keys only is completed with the model defaults."""
        write_entry(fs, '/mod', 'Mod:\n  name: test\n')

        ModEntry('/mod').write()

        entry = ModEntry('/mod')
        assert entry.name == 'test'
        assert entry.path_main == 'module/main.py'
        assert entry.pack_deps == ('alasio',)

    def test_declared_value_is_kept(self, fs):
        """A value the file declares survives the generation."""
        write_entry(fs, '/mod', 'Mod:\n  name: test\n  path_assets: res\n')

        ModEntry('/mod').write()

        assert ModEntry('/mod').path_assets == 'res'

    def test_unknown_key_is_dropped(self, fs):
        """A key the model does not know is not written back."""
        write_entry(fs, '/mod', 'Mod:\n  name: test\nfuture_key: 1\n')

        ModEntry('/mod').write()

        assert 'future_key' not in open(ModEntry('/mod').file, encoding='utf-8').read()

    def test_stages_the_file(self, fs):
        """A changed file is staged, an unchanged one is not."""
        entry = ModEntry('/mod', data=ModEntryModel.build(name='test'))
        staged = []

        entry.write(gitadd=type('G', (), {'stage_add': lambda self, f: staged.append(f)})())
        assert len(staged) == 1

        entry.write(gitadd=type('G', (), {'stage_add': lambda self, f: staged.append(f)})())
        assert len(staged) == 1


class TestModel:
    """The yaml model, the declaration the writer maintains."""

    def test_every_field_has_help(self):
        """Every key of the declaration carries a comment, produced from the model."""
        metadata = get_field_metadata(ModEntryModel)

        for name in ModEntryModel.__struct_fields__:
            meta = metadata.get(name)
            help_text = meta.extra.get('help') if meta else None
            assert help_text, f'{name} has no help text'
            # the help is what the writer emits, it must be usable text
            assert format_i18n(help_text)

    def test_model_is_default_constructible(self):
        """The writer builds a template of the model; a read refuses the empty name."""
        assert ModEntryModel().Mod.name == ''
        with pytest.raises(ModEntryError, match='name is required'):
            ModEntry.from_source('')


class TestAlasioEntry:
    """The entry of the library itself, a value of the code."""

    def test_singleton(self):
        """The entry of the library is created once."""
        assert ModEntry.alasio is ModEntry.alasio

    def test_value(self):
        """The library declares itself."""
        entry = ModEntry.alasio

        assert entry.name == 'alasio'
        assert entry.root == env.ALASIO_ROOT
        assert entry.path_config == ALASIO_CONFIG
        assert list(entry.gui_language) == ['zh-CN', 'en-US', 'ja-JP', 'zh-TW', 'en-ES']
        assert tuple(parse_filter(entry.scheduler_priority)) == ('RestartDevice', 'RestartGame')

    def test_the_library_is_not_a_mod(self):
        """The library is named by the code, it declares no mod entry."""
        assert ModEntry.is_mod(env.ALASIO_ROOT) is False

    def test_shipped_file_matches_the_code(self):
        """The declaration shipped with the package is the one the code declares.

        The file is written by the config generation, this keeps the shipped
        file and the constant from drifting apart.
        """
        shipped = ModEntry(env.ALASIO_ROOT, ALASIO_CONFIG)

        assert shipped.data == ModEntry.alasio.data


class TestExampleModDeclaration:
    def test_entry(self):
        """The mod of the repository declares itself."""
        root = env.ALASIO_ROOT.joinpath('ExampleMod')
        entry = ModEntry(root)

        assert entry.name == 'example_mod'
        assert entry.root == root
        assert entry.path_config == MOD_CONFIG
        assert list(entry.asset_lang) == ['cn', 'en']
        assert list(entry.gui_language) == ['zh-CN', 'en-US']

    def test_scheduler_priority(self):
        """The declared order is the one the python declaration carried."""
        root = env.ALASIO_ROOT.joinpath('ExampleMod')

        assert parse_filter(ModEntry(root).scheduler_priority) == \
            parse_filter(EXAMPLE_PRIORITY_PYTHON)

    def test_found_from_a_file(self):
        """The mod is created from any file of its tree by its depth."""
        root = env.ALASIO_ROOT.joinpath('ExampleMod')
        file = root.joinpath('module/map/button.py')

        assert ModEntry.create(file, uppath=3).root == root

    def test_the_generated_module_creates_the_entry(self):
        """mod_entry.py next to the declaration is the entry the mod imports."""
        root = env.ALASIO_ROOT.joinpath('ExampleMod')
        file = root.joinpath('module/config/_index/mod_entry.py')

        assert file.isfile()
        assert ModEntry.create(file, uppath=4).root == root
