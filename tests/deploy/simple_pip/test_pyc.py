import sys
from importlib.util import cache_from_source

import pytest

from alasio.deploy.simple_pip.pyc import pyc_path
from alasio.ext.path import PathStr


class TestPycPath:
    """The bytecode of a source is cached in the __pycache__ folder of the source, PEP 3147."""

    @pytest.mark.parametrize(
        'source, folder, cache',
        [
            ('core.py', '__pycache__', 'core'),
            ('demo/core.py', 'demo/__pycache__', 'core'),
            ('demo/sub/deep/core.py', 'demo/sub/deep/__pycache__', 'core'),
            ('/env/Lib/site-packages/demo/core.py', '/env/Lib/site-packages/demo/__pycache__', 'core'),
            ('E:/env/Lib/site-packages/demo/core.py', 'E:/env/Lib/site-packages/demo/__pycache__', 'core'),
            # the dots of a file name are kept, the suffix of the source is dropped
            ('pkg.mod.py', '__pycache__', 'pkg.mod'),
            ('demo/pkg.mod.py', 'demo/__pycache__', 'pkg.mod'),
            # the suffix of a source is not case sensitive on a case insensitive filesystem
            ('Mod.PY', '__pycache__', 'Mod'),
        ],
    )
    def test_name(self, source, folder, cache):
        """The name of the pyc holds the source name, the cache tag of the interpreter and the .pyc suffix."""
        assert pyc_path(source) == f'{folder}/{cache}.{sys.implementation.cache_tag}.pyc'

    @pytest.mark.parametrize(
        'source, expected',
        [
            ('demo\\core.py', 'demo/__pycache__/core'),
            ('E:\\env\\demo\\core.py', 'E:/env/demo/__pycache__/core'),
        ],
    )
    def test_backslashes(self, source, expected):
        """A Windows path is answered with "/" separators, the paths of the project are posix."""
        assert pyc_path(source) == f'{expected}.{sys.implementation.cache_tag}.pyc'

    def test_str_subclass(self):
        """A path that is a str subclass, e.g. a PathStr, is answered like a str."""
        assert pyc_path(PathStr.new('demo/core.py')) == pyc_path('demo/core.py')

    @pytest.mark.parametrize(
        'optimization, suffix',
        [
            (1, '.opt-1'),
            (2, '.opt-2'),
            ('1', '.opt-1'),
            # 0 and "" are the level without any optimization
            (0, ''),
            ('', ''),
        ],
    )
    def test_optimization(self, optimization, suffix):
        """The manual optimization is a suffix of the name, the source of the pyc is the same."""
        tag = sys.implementation.cache_tag
        assert pyc_path('demo/core.py', optimization) == f'demo/__pycache__/core.{tag}{suffix}.pyc'

    def test_default_optimization(self):
        """Without an optimization argument, the level of the running interpreter is used."""
        tag = sys.implementation.cache_tag
        optimize = f'.opt-{sys.flags.optimize}' if sys.flags.optimize else ''
        assert pyc_path('demo/core.py') == f'demo/__pycache__/core.{tag}{optimize}.pyc'

    def test_pycache_prefix_ignored(self, monkeypatch):
        """A bytecode cache prefix moves the pyc of python, the installation keeps its own tree."""
        monkeypatch.setattr(sys, 'pycache_prefix', '/pycache')
        assert pyc_path('demo/core.py') == f'demo/__pycache__/core.{sys.implementation.cache_tag}.pyc'


class TestAgainstCacheFromSource:
    """Without a bytecode cache prefix, the answer is the one of python itself."""

    @pytest.mark.parametrize('optimization', [None, 1, 2])
    @pytest.mark.parametrize(
        'source',
        [
            'core.py',
            'demo/core.py',
            'demo/sub/deep/core.py',
            '/env/Lib/site-packages/demo/core.py',
            'pkg.mod.py',
        ],
    )
    def test_same_name_as_python(self, monkeypatch, source, optimization):
        """The in-tree name of cache_from_source() is reproduced, a prefix only moves its own cache."""
        monkeypatch.setattr(sys, 'pycache_prefix', None)
        expected = cache_from_source(source, optimization=optimization).replace('\\', '/')
        assert pyc_path(source, optimization) == expected


class TestInvalid:
    """Only a python source file has a pyc file."""

    @pytest.mark.parametrize(
        'source',
        [
            # not a source
            'demo/core.txt',
            'demo/core',
            'demo/core.pyc',
            # no module name
            'demo/.py',
            '.py',
            'demo/',
            '',
        ],
    )
    def test_not_a_source(self, source):
        """A path that is not a .py file of a module raises ValueError."""
        with pytest.raises(ValueError, match='Not a python source file'):
            pyc_path(source)
