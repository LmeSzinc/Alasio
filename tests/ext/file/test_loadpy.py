import pytest

from alasio.ext.file.loadpy import LOADPY_CACHE, loadpy


def test_loadpy_valid(tmp_path):
    """
    Test loadpy with a valid .py file
    """
    file = tmp_path / "valid.py"
    file.write_text("a = 1\ndef func(): return 2", encoding="utf-8")

    module = loadpy(str(file))
    assert module.a == 1
    assert module.func() == 2
    assert module.__name__ == "valid"


def test_loadpy_invalid_extension(tmp_path):
    """
    Test loadpy with an invalid file extension
    """
    file = tmp_path / "invalid.txt"
    file.write_text("a = 1", encoding="utf-8")

    with pytest.raises(ImportError, match='Not a ".py" file'):
        loadpy(str(file))


def test_loadpy_non_existent(tmp_path):
    """
    Test loadpy with a non-existent file
    """
    file = tmp_path / "non_existent.py"

    with pytest.raises(ImportError):
        loadpy(str(file))


def test_loadpy_relative_import(tmp_path):
    """
    Test loadpy with a file containing relative imports
    """
    file = tmp_path / "relative.py"
    file.write_text("from . import something", encoding="utf-8")

    with pytest.raises(ImportError, match='cannot load files that has relative import syntax'):
        loadpy(str(file))


def test_loadpy_directory(tmp_path):
    """
    Test loadpy with a directory path
    """
    directory = tmp_path / "dir.py"
    directory.mkdir()

    with pytest.raises(ImportError):
        loadpy(str(directory))


def test_loadpy_independent_modules(tmp_path):
    """
    Test that each loadpy call creates a new module
    """
    file = tmp_path / "independent.py"
    file.write_text("a = 1", encoding="utf-8")

    module1 = loadpy(str(file))
    module2 = loadpy(str(file))

    assert module1 is not module2
    module1.a = 2
    assert module2.a == 1


def test_loadpy_cache(tmp_path):
    """
    Test LOADPY_CACHE caching behavior
    """
    file = tmp_path / "cached.py"
    file.write_text("a = 1", encoding="utf-8")

    # First load
    module1 = LOADPY_CACHE.get(str(file))
    assert module1.a == 1

    # Second load from cache
    module2 = LOADPY_CACHE.get(str(file))
    assert module1 is module2

    # GC clears cache
    LOADPY_CACHE.gc()
    module3 = LOADPY_CACHE.get(str(file))
    assert module3 is not module1
    assert module3.a == 1


def test_loadpy_syntax_error(tmp_path):
    """
    Test loadpy with a file containing syntax error
    """
    file = tmp_path / "syntax_error.py"
    file.write_text("if True", encoding="utf-8")

    with pytest.raises(ImportError, match='Could not load file'):
        loadpy(str(file))


def test_loadpy_multiple_dots(tmp_path):
    """
    Test loadpy with a file name containing multiple dots
    """
    file = tmp_path / "foo.bar.py"
    file.write_text("a = 1", encoding="utf-8")

    module = loadpy(str(file))
    assert module.a == 1
    # get_stem("foo.bar.py") -> "foo.bar"
    assert module.__name__ == "foo.bar"
