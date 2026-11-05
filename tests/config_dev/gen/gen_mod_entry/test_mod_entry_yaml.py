"""
Tests of the declaration of a mod and its entry module
(GenModEntry.generate_mod_entry).

The declaration of a mod is maintained like config/deploy.yaml: the generator
writes it from the yaml model, so the file follows the model — the keys in the
order of the model, the comments of its ``help`` annotations, and the format
of YamlConfig. The write itself is ``ModEntry.write``, the generator only
hands it the git staging.

Next to it the generator writes ``mod_entry.py``, the one place the code of
the mod creates its entry: every module of the mod imports MOD_ENTRY from it
instead of computing the root from its own depth.
"""
from alasio.config.entry.mod_entry import MOD_CONFIG, ModEntry, ModEntryModel
from alasio.config_dev.gen.gen_mod_entry import GenModEntry
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy


def build(fs, entry=None):
    """
    Build a GenModEntry over the mod root of the fake filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        entry (ModEntry, optional): Entry to generate from, default to an
            entry of the fake root with the default values

    Returns:
        GenModEntry:
    """
    if entry is None:
        entry = ModEntry(fs.root_dir.path, data=ModEntryModel.build(name='test'))
    return GenModEntry(entry)


class TestGenerateModEntry:
    def test_writes_every_key_of_the_model(self, fs):
        """A mod without a declaration gets one, with every key of the model."""
        gen = build(fs)

        assert gen.generate_mod_entry() is True

        text = open(gen.mod_entry_file, encoding='utf-8').read()
        keys = [line.partition(':')[0] for line in text.splitlines()
                if line and not line.startswith(('#', '-', ' '))]
        assert keys == list(ModEntryModel.__struct_fields__)

    def test_comments_come_from_the_model(self, fs):
        """The help annotation of the model is the comment of the key."""
        gen = build(fs)

        gen.generate_mod_entry()

        text = open(gen.mod_entry_file, encoding='utf-8').read()
        assert '# Name of the mod, the key the framework identifies it by' in text
        assert '# Task order of the scheduler, a task runs before the tasks after it' in text

    def test_read_back_is_the_entry(self, fs):
        """The written declaration reads back to the entry it was written from."""
        entry = ModEntry(
            fs.root_dir.path,
            data=ModEntryModel.build(
                name='test',
                gui_language=['zh-CN', 'ja-JP'],
                pack_deps=['httpx'],
                scheduler_priority='A > B > C',
            ),
        )
        gen = build(fs, entry)

        gen.generate_mod_entry()

        assert ModEntry(fs.root_dir.path).data == entry.data

    def test_second_run_writes_nothing(self, fs):
        """The generator is idempotent: the file is written once."""
        gen = build(fs)
        assert gen.generate_mod_entry() is True

        assert gen.generate_mod_entry() is False

    def test_missing_key_is_added_with_the_default(self, fs):
        """A declaration of some keys only is completed with the model defaults."""
        gen = build(fs)
        fs.create_file(gen.mod_entry_file, contents='Mod:\n  name: test\n')

        gen.generate_mod_entry()

        entry = ModEntry(fs.root_dir.path)
        assert entry.name == 'test'
        assert entry.path_main == 'module/main.py'
        assert entry.pack_deps == ('alasio', 'msgspecerror')

    def test_declared_value_is_kept(self, fs):
        """A value the file declares survives the generation."""
        gen = build(fs, ModEntry(fs.root_dir.path, data=ModEntryModel.build(name='test', path_assets='res')))
        fs.create_file(gen.mod_entry_file, contents='Mod:\n  name: test\n  path_assets: res\n')

        gen.generate_mod_entry()

        assert ModEntry(fs.root_dir.path).path_assets == 'res'

    def test_unknown_key_is_dropped(self, fs):
        """A key the model does not know is not written back."""
        gen = build(fs)
        fs.create_file(gen.mod_entry_file, contents='Mod:\n  name: test\nfuture_key: 1\n')

        gen.generate_mod_entry()

        assert 'future_key' not in open(gen.mod_entry_file, encoding='utf-8').read()

    def test_stages_the_file(self, fs):
        """A changed file is staged, an unchanged one is not."""
        gen = build(fs)
        gitadd = GitAddSpy()

        gen.generate_mod_entry(gitadd=gitadd)
        assert [PathStr.new(f).name for f in gitadd.files] == ['mod_entry.yaml', 'mod_entry.py']

        gen.generate_mod_entry(gitadd=gitadd)
        assert [PathStr.new(f).name for f in gitadd.files] == ['mod_entry.yaml', 'mod_entry.py']

    def test_file_of_the_entry_layout(self, fs):
        """The declaration is written under the config folder of the mod."""
        gen = build(fs)

        assert PathStr.new(gen.mod_entry_file) == \
            PathStr.new(fs.root_dir.path).joinpath(f'{MOD_CONFIG}/_index/mod_entry.yaml')

    def test_alasio_layout(self, fs):
        """The entry of the library writes its declaration under alasio/config."""
        gen = GenModEntry(ModEntry.alasio)

        file = PathStr.new(gen.mod_entry_file)
        assert file.name == 'mod_entry.yaml'
        assert file.uppath(3).name == 'alasio'

    def test_alasio_layout_writes_no_module(self, fs):
        """The entry of the library is a value of its code, no module of it."""
        gen = GenModEntry(ModEntry.alasio)

        gen.generate_mod_entry()

        assert not PathStr.new(gen.mod_entry_py_file).exists()

    def test_logs_the_write(self, fs):
        """A write is reported, a skipped one is silent."""
        gen = build(fs)

        with logger.mock_capture_writer() as capture:
            gen.generate_mod_entry()
        assert capture.fd.any_contains('Write file')

        with logger.mock_capture_writer() as capture:
            gen.generate_mod_entry()
        assert not capture.fd.any_contains('Write file')


class TestGenerateModEntryPy:
    """The module that exposes the entry to the code of the mod."""

    def test_file_content(self, fs):
        """The module creates the entry from its own depth, 4 levels below the root."""
        gen = build(fs)

        assert gen.generate_mod_entry_py() is True

        assert open(gen.mod_entry_py_file, encoding='utf-8').read() == """\
from alasio.config.entry.mod_entry import ModEntry

# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

MOD_ENTRY = ModEntry.create(__file__, uppath=4)
"""

    def test_next_to_the_declaration(self, fs):
        """The module lives next to mod_entry.yaml, in the _index folder."""
        gen = build(fs)

        assert PathStr.new(gen.mod_entry_py_file) == \
            PathStr.new(fs.root_dir.path).joinpath(f'{MOD_CONFIG}/_index/mod_entry.py')
        assert PathStr.new(gen.mod_entry_py_file).uppath(1) == \
            PathStr.new(gen.mod_entry_file).uppath(1)

    def test_second_run_writes_nothing(self, fs):
        """The generator is idempotent: the module is written once."""
        gen = build(fs)

        assert gen.generate_mod_entry_py() is True
        assert gen.generate_mod_entry_py() is False


class TestModEntryBlockScalar:
    def test_multiline_priority_stays_a_block(self, fs):
        """A multi-line order is written as a block scalar, not escaped."""
        entry = ModEntry(
            fs.root_dir.path,
            data=ModEntryModel.build(name='test', scheduler_priority='A > B\n> C'),
        )
        gen = build(fs, entry)

        gen.generate_mod_entry()

        text = open(gen.mod_entry_file, encoding='utf-8').read()
        assert '  scheduler_priority: |-\n    A > B\n    > C\n' in text
        assert ModEntry(fs.root_dir.path).scheduler_priority == 'A > B\n> C'

    def test_single_line_priority_is_plain(self, fs):
        """A single-line order is a plain scalar, the file stays readable."""
        entry = ModEntry(
            fs.root_dir.path,
            data=ModEntryModel.build(name='test', scheduler_priority='A > B'),
        )
        gen = build(fs, entry)

        gen.generate_mod_entry()

        text = open(gen.mod_entry_file, encoding='utf-8').read()
        assert '  scheduler_priority: A > B\n' in text
