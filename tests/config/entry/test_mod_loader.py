"""
Tests of ModLoader: the mods the backend loads from the project root.

The loader reads the declaration of the project root itself and of the folders
next to it (``MOD_FOLDERS``). Only a folder that declares a readable
``module/config/_index/mod_entry.yaml`` is a mod: a folder without one is not a
mod, and a folder whose declaration cannot be read is reported and skipped, so
one broken mod never takes the backend down.
"""
import pytest

from alasio.config.entry.loader import ModLoader
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

MOD_ENTRY_FILE = 'module/config/_index/mod_entry.yaml'


def make_root(fs):
    """
    Build a project root with three folders next to it.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture

    Returns:
        PathStr: The project root
    """
    root = PathStr.new(fs.root_dir.path).joinpath('project')
    fs.create_dir(root)
    return root


def write_mod(fs, root, folder, name):
    """
    Declare a mod in a folder of the project root.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        root (PathStr): The project root
        folder (str): Folder of the mod
        name (str): Name the declaration gives the mod

    Returns:
        PathStr: The root of the mod
    """
    mod_root = root.joinpath(folder)
    fs.create_file(mod_root.joinpath(MOD_ENTRY_FILE), contents=f'name: {name}\n')
    return mod_root


class TestDictMod:
    """The mods of the project root, keyed by the name of their declaration."""

    def test_folders_with_a_declaration(self, fs, monkeypatch):
        """A folder that declares a mod is loaded, the others are skipped."""
        root = make_root(fs)
        monkeypatch.setattr(env, 'PROJECT_ROOT', root)
        write_mod(fs, root, 'mod_a', 'a')
        write_mod(fs, root, 'mod_b', 'b')
        loader = ModLoader(root, mod_folders=('mod_a', 'missing', 'mod_b'))

        assert list(loader.dict_mod) == ['a', 'b']

    def test_broken_declaration_is_skipped(self, fs, monkeypatch):
        """A declaration that cannot be read is reported, the other mods load."""
        root = make_root(fs)
        monkeypatch.setattr(env, 'PROJECT_ROOT', root)
        write_mod(fs, root, 'mod_a', 'a')
        fs.create_file(
            root.joinpath('mod_b').joinpath(MOD_ENTRY_FILE), contents='name: [unclosed\n')
        loader = ModLoader(root, mod_folders=('mod_a', 'mod_b'))

        with logger.mock_capture_writer() as capture:
            assert list(loader.dict_mod) == ['a']
        assert capture.fd.any_contains('Invalid mod entry, mod skipped')

    def test_a_declaration_must_name_the_mod(self, fs, monkeypatch):
        """A declaration without a name is as broken as an unreadable one."""
        root = make_root(fs)
        monkeypatch.setattr(env, 'PROJECT_ROOT', root)
        fs.create_file(
            root.joinpath('mod_a').joinpath(MOD_ENTRY_FILE), contents='path_main: module/main.py\n')
        loader = ModLoader(root, mod_folders=('mod_a',))

        with logger.mock_capture_writer() as capture:
            assert loader.dict_mod == {}
        assert capture.fd.any_contains('name is required')

    def test_duplicate_name_is_loaded_once(self, fs, monkeypatch):
        """Two folders that declare the same name are one mod."""
        root = make_root(fs)
        monkeypatch.setattr(env, 'PROJECT_ROOT', root)
        write_mod(fs, root, 'mod_a', 'same')
        write_mod(fs, root, 'mod_b', 'same')
        loader = ModLoader(root, mod_folders=('mod_a', 'mod_b'))

        assert list(loader.dict_mod) == ['same']


class TestSelfMod:
    """The mod the project root declares itself."""

    def test_no_declaration(self, fs):
        """A project root without a declaration is not a mod."""
        root = make_root(fs)
        loader = ModLoader(root, mod_folders=())

        assert loader.self_mod is None

    def test_declared(self, fs):
        """A project root that declares a mod is the self mod."""
        root = make_root(fs)
        fs.create_file(root.joinpath(MOD_ENTRY_FILE), contents='name: self_mod\n')
        loader = ModLoader(root, mod_folders=())

        assert loader.self_mod.name == 'self_mod'
        assert list(loader.dict_mod) == ['self_mod']

    def test_broken_declaration_raises(self, fs):
        """A broken declaration of the project root fails the loader, not silently."""
        root = make_root(fs)
        fs.create_file(root.joinpath(MOD_ENTRY_FILE), contents='name: [unclosed\n')
        loader = ModLoader(root, mod_folders=())

        with pytest.raises(ValueError, match='invalid yaml'):
            _ = loader.self_mod


class TestRealMods:
    """The mods shipped with the repository."""

    def test_example_mod(self, monkeypatch):
        """ExampleMod is declared and next to the project root of the library."""
        monkeypatch.setattr(env, 'PROJECT_ROOT', env.ALASIO_ROOT)
        loader = ModLoader(env.ALASIO_ROOT, mod_folders=('ExampleMod',))

        assert list(loader.dict_mod) == ['example_mod']
