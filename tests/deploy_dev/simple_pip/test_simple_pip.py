import csv
import os
import re
import sys
import sysconfig
import zipfile
from importlib.util import cache_from_source

import pytest
from conftest import (  # noqa: F401
    abs_path, build_wheel, create_dist, list_files, list_folders, pyc_compile, sha256_record, site_packages
)

from alasio.deploy_dev.simple_pip import DistInfo, SimplePip
from alasio.deploy_dev.simple_pip.simple_pip import Scheme, probe_python
from alasio.ext.concurrent.cmd import CmdlineError, CmdlineResultStr
from alasio.ext.path import PathStr
from alasio.ext.path.calc import to_posix
from alasio.testing.filesystem import fs  # noqa: F401


def pyc_path(path):
    """
    Path of the .pyc file python generates for a source file.

    Args:
        path (str): Path of the .py file, relative to site-packages

    Returns:
        str: Relative posix path of the .pyc file
    """
    return to_posix(cache_from_source(path))


def read_record(site, name='demo-1.0.dist-info'):
    """
    Read the RECORD of an installation the way pip reads it.

    pip reads the RECORD with csv.reader() and joins the paths to
    site-packages, see _iter_declared_entries_from_record() of
    pip/_internal/metadata/base.py.

    Args:
        site (str): Path of site-packages
        name (str): Name of the .dist-info directory

    Returns:
        list[tuple[str, str, str]]: Rows of (path, sha256, size)
    """
    with open(f'{site}/{name}/RECORD', encoding='utf-8') as f:
        return [tuple(row) for row in csv.reader(f.read().splitlines())]


class TestInit:
    def test_init_posix_path(self):
        """A posix path is kept as is, a trailing separator is stripped."""
        pip = SimplePip('/env/Lib/site-packages/')
        assert pip.site_packages == '/env/Lib/site-packages'

    @pytest.mark.skipif(os.name != 'nt', reason='native path of Windows')
    def test_init_native_windows_path(self):
        """A native Windows path is normalized to the posix style of the project.

        uppath() of PathStr splits on "/" only, a site-packages left with
        the backslashes of the caller gives an empty prefix and breaks the
        install of a .data directory.
        """
        pip = SimplePip('E:\\env\\Lib\\site-packages')
        assert pip.site_packages == 'E:/env/Lib/site-packages'
        assert pip.prefix == 'E:/env'

    def test_init_relative_path(self):
        """The install directory has to be absolute, it is not resolved against the cwd."""
        with pytest.raises(ValueError, match='is not an absolute path'):
            SimplePip('env/Lib/site-packages')

    def test_init_prefix(self):
        """The prefix of the environment is given by the caller when known."""
        pip = SimplePip('/env/Lib/site-packages', prefix='/env')
        assert pip.prefix == '/env'
        assert pip.scheme.data == '/env'

    def test_init_scheme(self):
        """The scheme of the environment is given by the caller when known."""
        scheme = Scheme(
            purelib='/env/Lib/site-packages', platlib='/env/Lib/site-packages',
            scripts='/env/other-scripts', data='/env', include='/env/Include',
        )
        pip = SimplePip('/env/Lib/site-packages', scheme=scheme)
        assert pip.scheme is scheme

    def test_init_prefix_default(self):
        """Without a prefix, it is derived from the layout of site-packages."""
        site = '/env/Lib/site-packages' if os.name == 'nt' else '/env/lib/python3.8/site-packages'
        assert SimplePip(site).prefix == '/env'

    def test_init_prefix_relative(self):
        """The prefix has to be absolute as well."""
        with pytest.raises(ValueError, match='prefix is not an absolute path'):
            SimplePip('/env/Lib/site-packages', prefix='env')


class TestFromPython:
    # The scheme the probe of a real interpreter returns, the paths of the
    # environment itself, they contradict the layout of site-packages on purpose
    SCHEME = '{"purelib": "/env/lib/python3.8/site-packages", "platlib": "/env/lib64/python3.8/site-packages",'
    SCHEME += ' "scripts": "/env/bin", "data": "/env", "include": "/env/include/python3.8"}'

    @staticmethod
    def patch_probe(monkeypatch, stdout):
        """Patch the output of the probe running the target python."""
        def run_cmd(cmd, **kwargs):
            return CmdlineResultStr(cmd=list(cmd), returncode=0, stdout=stdout)

        monkeypatch.setattr('alasio.deploy_dev.simple_pip.simple_pip.run_cmd', run_cmd)

    def test_from_python(self, monkeypatch):
        """The site-packages entry is used, the random output of the environment is ignored."""
        self.patch_probe(monkeypatch, (
            'random output of the user command line environment\r\n'
            'xxxsitepackage {"site_packages": ["/env", "/env/lib/python3.8/site-packages"],'
            ' "prefix": "/env"}\r\n'
        ))
        pip = SimplePip.from_python('python.exe')
        assert pip.site_packages == '/env/lib/python3.8/site-packages'
        assert pip.prefix == '/env'

    def test_from_python_scheme(self, monkeypatch):
        """The paths of the probed scheme are used, not the ones derived from the layout."""
        self.patch_probe(monkeypatch, (
            'xxxsitepackage {"site_packages": ["/env/lib/python3.8/site-packages"],'
            f' "prefix": "/env", "scheme": {self.SCHEME}}}\n'
        ))
        pip = SimplePip.from_python('python.exe')
        assert pip.scheme.platlib == '/env/lib64/python3.8/site-packages'
        assert pip.scheme.scripts == '/env/bin'
        assert pip.scheme.data == '/env'
        assert pip.scheme.include == '/env/include/python3.8'
        assert pip.scheme.dir_of('headers', 'demo') == '/env/include/python3.8/demo'

    def test_from_python_prefix(self, monkeypatch):
        """The prefix of the interpreter is used, not the one derived from the layout.

        A virtual environment keeps its python executable in Scripts/ on
        Windows and in bin/ on Linux, the directory of the executable is not
        the prefix of the environment, sys.prefix is.
        """
        self.patch_probe(monkeypatch, (
            'xxxsitepackage {"site_packages": ["/env/lib/python3.8/site-packages"],'
            ' "prefix": "/env/other"}\n'
        ))
        pip = SimplePip.from_python('python.exe')
        assert pip.site_packages == '/env/lib/python3.8/site-packages'
        assert pip.prefix == '/env/other'

    def test_from_python_prefix_missing(self, monkeypatch):
        """An output without a prefix and a scheme falls back to the derived ones."""
        self.patch_probe(monkeypatch, 'xxxsitepackage {"site_packages": ["/env/Lib/site-packages"]}\n')
        pip = SimplePip.from_python('python.exe')
        assert pip.prefix == ('/env' if os.name == 'nt' else '')
        assert pip.scheme.scripts == ('/env/Scripts' if os.name == 'nt' else '/bin')

    def test_from_python_scheme_without_site_packages(self, monkeypatch):
        """The listing holds no site-packages directory, the purelib of the scheme is used."""
        self.patch_probe(monkeypatch, (
            'xxxsitepackage {"site_packages": ["/env"], "prefix": "/env",'
            f' "scheme": {self.SCHEME}}}\n'
        ))
        pip = SimplePip.from_python('python.exe')
        assert pip.site_packages == '/env/lib/python3.8/site-packages'
        assert pip.prefix == '/env'

    def test_from_python_dist_packages(self, monkeypatch):
        """Debian based distributions name the directory dist-packages."""
        self.patch_probe(monkeypatch, (
            'xxxsitepackage {"site_packages": ["/usr/lib/python3/dist-packages"], "prefix": "/usr"}\n'
        ))
        pip = SimplePip.from_python('python3')
        assert pip.site_packages == '/usr/lib/python3/dist-packages'
        assert pip.prefix == '/usr'

    @pytest.mark.skipif(os.name != 'nt', reason='native path of Windows')
    def test_from_python_windows_path(self, monkeypatch):
        """The separators of the probe output are normalized to the posix style."""
        self.patch_probe(monkeypatch, (
            'xxxsitepackage {"site_packages": ["E:\\\\env\\\\Lib\\\\site-packages"],'
            ' "prefix": "E:\\\\env"}\n'
        ))
        pip = SimplePip.from_python('python.exe')
        assert pip.site_packages == 'E:/env/Lib/site-packages'
        assert pip.prefix == 'E:/env'

    def test_from_python_no_site_packages(self, monkeypatch):
        """An environment without a site-packages directory and without a scheme."""
        self.patch_probe(monkeypatch, 'xxxsitepackage {"site_packages": ["/env"], "prefix": "/env"}\n')
        with pytest.raises(ValueError, match='Failed to get sitepackage'):
            SimplePip.from_python('python.exe')

    def test_from_python_invalid_json(self, monkeypatch):
        """Truncated output of the probe."""
        self.patch_probe(monkeypatch, 'xxxsitepackage\n')
        with pytest.raises(ValueError, match='Invalid probe return'):
            SimplePip.from_python('python.exe')

    def test_from_python_invalid_output(self, monkeypatch):
        """An output that is not the json object the probe prints."""
        self.patch_probe(monkeypatch, 'xxxsitepackage ["/env/lib/python3.8/site-packages"]\n')
        with pytest.raises(ValueError, match='Invalid probe return'):
            SimplePip.from_python('python.exe')

    def test_from_python_unrunnable(self, monkeypatch):
        """An interpreter that can not be run."""
        def run_cmd(cmd, **kwargs):
            raise CmdlineError(msg='Command not found: not_exists.exe', cmd=list(cmd))

        monkeypatch.setattr('alasio.deploy_dev.simple_pip.simple_pip.run_cmd', run_cmd)
        with pytest.raises(ValueError, match='Failed to probe not_exists.exe'):
            SimplePip.from_python('not_exists.exe')

    def test_probe_python(self):
        """The probe of the interpreter running the tests, the real output of the real one."""
        info = probe_python(sys.executable)
        assert sorted(info) == ['prefix', 'scheme', 'site_packages']
        assert info['prefix'] == os.path.abspath(sys.prefix)
        assert info['scheme']['purelib']
        assert info['scheme']['scripts']
        # The scheme of the running interpreter, not a path derived from the layout
        assert PathStr.new(info['scheme']['scripts']) == PathStr.new(sysconfig.get_paths()['scripts'])
        for path in info['site_packages']:
            assert os.path.isabs(path)

    def test_from_python_prefix_missing_scheme(self, monkeypatch):
        """A scheme that does not hold every path falls back to the layout."""
        self.patch_probe(monkeypatch, (
            'xxxsitepackage {"site_packages": ["/env/lib/python3.8/site-packages"],'
            ' "prefix": "/env", "scheme": {"purelib": "/env/lib/python3.8/site-packages"}}\n'
        ))
        pip = SimplePip.from_python('python.exe')
        assert pip.scheme.data == '/env'
        assert pip.scheme.scripts == ('/env/Scripts' if os.name == 'nt' else '/env/bin')


class TestScheme:
    def test_scheme(self):
        """The paths of the scheme are the paths of the environment, posix style."""
        scheme = Scheme(
            purelib='/env/Lib/site-packages', platlib='/env/Lib/site-packages',
            scripts='/env/Scripts', data='/env', include='/env/Include',
        )
        assert scheme.purelib == '/env/Lib/site-packages'
        assert repr(scheme) == (
            '<Scheme purelib=/env/Lib/site-packages platlib=/env/Lib/site-packages '
            'scripts=/env/Scripts data=/env include=/env/Include>')

    def test_scheme_dir_of(self):
        """The categories of the .data directory of a wheel, PEP 427."""
        scheme = Scheme(
            purelib='/env/Lib/site-packages', platlib='/env/Lib64/site-packages',
            scripts='/env/Scripts', data='/env', include='/env/Include',
        )
        assert scheme.dir_of('purelib', 'demo') == '/env/Lib/site-packages'
        assert scheme.dir_of('platlib', 'demo') == '/env/Lib64/site-packages'
        assert scheme.dir_of('scripts', 'demo') == '/env/Scripts'
        assert scheme.dir_of('data', 'demo') == '/env'
        # The headers of a distribution go to their own directory, like pip
        assert scheme.dir_of('headers', 'demo') == '/env/Include/demo'

    def test_scheme_from_dict(self):
        """The scheme probed from an interpreter."""
        scheme = Scheme.from_dict({
            'purelib': '/env/lib/python3.8/site-packages',
            'platlib': '/env/lib64/python3.8/site-packages',
            'scripts': '/env/bin',
            'data': '/env',
            'include': '/env/include/python3.8',
            'stdlib': '/usr/lib/python3.8',
        })
        assert scheme.scripts == '/env/bin'
        assert scheme.include == '/env/include/python3.8'

    @pytest.mark.parametrize("payload", [
        None,
        [],
        {},
        # A path of the scheme is missing, or is not a string
        {'purelib': '/env/lib/python3.8/site-packages', 'platlib': '/env/l', 'scripts': '/env/bin', 'data': '/env'},
        {'purelib': '/env/lib/python3.8/site-packages', 'platlib': None, 'scripts': '/env/bin', 'data': '/env',
         'include': '/env/include'},
    ])
    def test_scheme_from_dict_invalid(self, payload):
        """A payload that does not hold every path is rejected, the caller falls back."""
        assert Scheme.from_dict(payload) is None

    @pytest.mark.parametrize("layout, site_rel, expected", [
        ('nt', '/env/Lib/site-packages', {
            'purelib': '/env/Lib/site-packages', 'platlib': '/env/Lib/site-packages',
            'scripts': '/env/Scripts', 'data': '/env', 'include': '/env/Include'}),
        ('posix', '/env/lib/python3.8/site-packages', {
            'purelib': '/env/lib/python3.8/site-packages', 'platlib': '/env/lib/python3.8/site-packages',
            'scripts': '/env/bin', 'data': '/env', 'include': '/env/include/python3.8'}),
    ])
    def test_scheme_from_layout(self, monkeypatch, layout, site_rel, expected):
        """The fallback derives the default scheme from the layout of site-packages."""
        monkeypatch.setattr('alasio.deploy_dev.simple_pip.simple_pip.WINDOWS', layout == 'nt')
        scheme = Scheme.from_layout(site_rel)
        assert {
            'purelib': scheme.purelib, 'platlib': scheme.platlib,
            'scripts': scheme.scripts, 'data': scheme.data, 'include': scheme.include,
        } == expected


class TestDistInfoLookup:
    def test_dist_info(self, fs):
        """The .dist-info directories of the environment are listed."""
        site = site_packages(fs)
        create_dist(fs, {'demo/__init__.py': b''})
        create_dist(fs, {'other/__init__.py': b''}, name='other', version='2.0')
        pip = SimplePip(site)
        assert {str(name): str(folder) for name, folder in pip.dist_info.items()} == {
            'demo': f'{site}/demo-1.0.dist-info',
            'other': f'{site}/other-2.0.dist-info',
        }

    def test_dist_info_ignores_others(self, fs):
        """Directories that are not a .dist-info, and .dist-info without a name."""
        site = site_packages(fs)
        create_dist(fs, {'demo/__init__.py': b''})
        fs.create_file(f'{site}/not_a_dist/file.py', contents=b'')
        fs.create_file(f'{site}/broken.dist-info/file.py', contents=b'')
        assert list(SimplePip(site).dist_info) == ['demo']

    @pytest.mark.parametrize("name", ['demo', 'Demo', 'DEMO'])
    def test_get_dist_info(self, fs, name):
        """The lookup is case insensitive."""
        site = site_packages(fs)
        create_dist(fs, {'demo/__init__.py': b''})
        assert SimplePip(site).get_dist_info(name) == f'{site}/demo-1.0.dist-info'

    @pytest.mark.parametrize("name", ['my-pkg', 'my_pkg', 'my.pkg', 'My.Pkg'])
    def test_get_dist_info_normalized(self, fs, name):
        """PEP 427 replaces "-" and "." with "_" in the name of the .dist-info."""
        site = site_packages(fs)
        create_dist(fs, {'my_pkg/__init__.py': b''}, name='my_pkg')
        assert SimplePip(site).get_dist_info(name) == f'{site}/my_pkg-1.0.dist-info'

    def test_get_dist_info_missing(self, fs):
        """An environment without the package."""
        assert SimplePip(site_packages(fs)).get_dist_info('demo') is None


class TestInstall:
    def test_install(self, fs, pyc_compile):
        """Every file of the wheel is installed, with the INSTALLER and the RECORD of pip."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo/core.py': b'def run():\n    return 1\n',
        })
        SimplePip(site).install(wheel)
        assert list_files(site) == [
            'demo-1.0.dist-info/INSTALLER',
            'demo-1.0.dist-info/METADATA',
            'demo-1.0.dist-info/RECORD',
            'demo-1.0.dist-info/WHEEL',
            'demo-1.0.dist-info/top_level.txt',
            'demo/__init__.py',
            pyc_path('demo/__init__.py'),
            pyc_path('demo/core.py'),
            'demo/core.py',
        ]
        with open(f'{site}/demo-1.0.dist-info/INSTALLER', 'rb') as f:
            assert f.read() == b'pip\n'

    def test_install_record(self, fs, pyc_compile):
        """The RECORD lists the files, the checksum of the content and the size of the file."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {'demo/__init__.py': b'a = 1\n'})
        SimplePip(site).install(wheel)
        rows = read_record(site)
        assert [path for path, _, _ in rows] == [
            'demo/__init__.py',
            pyc_path('demo/__init__.py'),
            'demo-1.0.dist-info/INSTALLER',
            'demo-1.0.dist-info/METADATA',
            'demo-1.0.dist-info/RECORD',
            'demo-1.0.dist-info/WHEEL',
            'demo-1.0.dist-info/top_level.txt',
        ]
        assert ('demo/__init__.py', 'sha256=y3i9ihf3t1H-DUZjNm3LwlcgQDPvfd1ksfKWlXO1suI', '6') in rows
        assert (pyc_path('demo/__init__.py'), '', '') in rows
        assert ('demo-1.0.dist-info/INSTALLER', 'sha256=zuuue4knoyJ-UwPPXg8fezS7VCrXJQrAP7zeNuwvFQg', '4') in rows
        assert ('demo-1.0.dist-info/RECORD', '', '') in rows
        # The checksums of the RECORD are the checksums of the installed files
        for path, sha256, size in rows:
            with open(f'{site}/{path}', 'rb') as f:
                content = f.read()
            if sha256:
                assert sha256 == sha256_record(content)
            if size:
                assert size == str(len(content))

    def test_install_record_bytes(self, fs, pyc_compile):
        """The bytes of the RECORD of an installation, the .pyc rows are the only dynamic ones."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            # No .py file, so no .pyc is compiled at install time
            'demo/data.json': b'{"a": 1}\n',
            'demo-1.0.dist-info/METADATA': b'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n',
            'demo-1.0.dist-info/WHEEL': b'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n',
            'demo-1.0.dist-info/top_level.txt': b'demo\n',
        })
        SimplePip(site).install(wheel)
        with open(f'{site}/demo-1.0.dist-info/RECORD', 'rb') as f:
            assert f.read() == b"""\
demo/data.json,sha256=6MYo7cmWjvDGaPVOC6JjazVQM1frGsoN3IKK6s5DL2c,9
demo-1.0.dist-info/INSTALLER,sha256=zuuue4knoyJ-UwPPXg8fezS7VCrXJQrAP7zeNuwvFQg,4
demo-1.0.dist-info/METADATA,sha256=NKonpgZPlYtBt4GiF_KQ_Qq1-WUeK25Kzas0G3uTwwU,46
demo-1.0.dist-info/RECORD,,
demo-1.0.dist-info/WHEEL,sha256=SheWtM-HUEnwmto_Cq52y9Tg9AiYd4hdXN1bxkX9qNg,57
demo-1.0.dist-info/top_level.txt,sha256=65wmuu5H8Z5Jk6d7ypNtD_CeNVqC09t5vxVOv_GoBgQ,5
"""

    def test_install_empty_file(self, fs, pyc_compile):
        """An empty file is recorded with the checksum of the empty content."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {'demo/empty.py': b''})
        SimplePip(site).install(wheel)
        assert os.path.getsize(f'{site}/demo/empty.py') == 0
        assert ('demo/empty.py', 'sha256=47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU', '0') in read_record(site)

    def test_install_reinstall(self, fs, pyc_compile):
        """The installation replaces the previous one, the files of it are removed."""
        site = site_packages(fs)
        old = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo/old.py': b'a = 2\n',
        })
        new = build_wheel('/w/demo-2.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo/new.py': b'a = 3\n',
        }, version='2.0')
        pip = SimplePip(site)
        pip.install(old)
        pip.install(new)
        assert list_files(site) == [
            'demo-2.0.dist-info/INSTALLER',
            'demo-2.0.dist-info/METADATA',
            'demo-2.0.dist-info/RECORD',
            'demo-2.0.dist-info/WHEEL',
            'demo-2.0.dist-info/top_level.txt',
            'demo/__init__.py',
            pyc_path('demo/__init__.py'),
            pyc_path('demo/new.py'),
            'demo/new.py',
        ]

    def test_install_keeps_other_distribution(self, fs, pyc_compile):
        """The files of an other distribution of the environment are not removed."""
        site = site_packages(fs)
        create_dist(fs, {'shared/other.py': b'a = 1\n'}, name='nsb')
        wheel = build_wheel('/w/nsa-1.0-py3-none-any.whl', {'shared/nsa.py': b'a = 2\n'}, name='nsa')
        SimplePip(site).install(wheel)
        assert 'shared/other.py' in list_files(site)
        assert 'shared/nsa.py' in list_files(site)
        assert 'nsb-1.0.dist-info/RECORD' in list_files(site)

    def test_install_entry_points_warning(self, fs, pyc_compile, capsys):
        """The scripts declared by entry_points.txt are not generated."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo-1.0.dist-info/entry_points.txt': b'[console_scripts]\ndemo = demo:main\n',
        })
        SimplePip(site).install(wheel)
        assert capsys.readouterr().out == (
            f'Package not exist: demo\n'
            f'Warning: console_scripts of the wheel are not generated by SimplePip\n'
            f'Installing demo to {site}\n'
            f'Compiling 1 py files\n'
            f'Successfully installed demo\n'
        )

    def test_install_executable(self, fs, pyc_compile):
        """The executable permission of a member is kept, pip does the same."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo-1.0.data/scripts/demo-tool': b'#!python\n',
            'demo-1.0.data/scripts/demo.conf': b'answer = 42\n',
        }, executable=['demo-1.0.data/scripts/demo-tool'])
        SimplePip(site).install(wheel)
        assert os.stat(abs_path(fs, '/env/Scripts/demo-tool')).st_mode & 0o111 == 0o111
        assert os.stat(abs_path(fs, '/env/Scripts/demo.conf')).st_mode & 0o111 == 0

    @pytest.mark.parametrize("layout, site_rel, category, member, target_rel, record_rel", [
        # Windows layout: <prefix>/Lib/site-packages
        ('nt', '/env/Lib/site-packages', 'purelib', 'demo/x.py',
         '/env/Lib/site-packages/demo/x.py', 'demo/x.py'),
        ('nt', '/env/Lib/site-packages', 'platlib', 'demo/x.py',
         '/env/Lib/site-packages/demo/x.py', 'demo/x.py'),
        ('nt', '/env/Lib/site-packages', 'scripts', 'demo-tool',
         '/env/Scripts/demo-tool', '../../Scripts/demo-tool'),
        ('nt', '/env/Lib/site-packages', 'data', 'share/demo/data.txt',
         '/env/share/demo/data.txt', '../../share/demo/data.txt'),
        ('nt', '/env/Lib/site-packages', 'headers', 'demo.h',
         '/env/Include/demo/demo.h', '../../Include/demo/demo.h'),
        # POSIX layout: <prefix>/lib/python3.8/site-packages
        ('posix', '/env/lib/python3.8/site-packages', 'purelib', 'demo/x.py',
         '/env/lib/python3.8/site-packages/demo/x.py', 'demo/x.py'),
        ('posix', '/env/lib/python3.8/site-packages', 'platlib', 'demo/x.py',
         '/env/lib/python3.8/site-packages/demo/x.py', 'demo/x.py'),
        ('posix', '/env/lib/python3.8/site-packages', 'scripts', 'demo-tool',
         '/env/bin/demo-tool', '../../../bin/demo-tool'),
        ('posix', '/env/lib/python3.8/site-packages', 'data', 'share/demo/data.txt',
         '/env/share/demo/data.txt', '../../../share/demo/data.txt'),
        ('posix', '/env/lib/python3.8/site-packages', 'headers', 'demo.h',
         '/env/include/python3.8/demo/demo.h', '../../../include/python3.8/demo/demo.h'),
    ])
    def test_install_data_categories(self, fs, pyc_compile, monkeypatch, layout, site_rel, category,
                                     member, target_rel, record_rel, ):
        """The .data directories are installed to the locations of the scheme of pip."""
        monkeypatch.setattr('alasio.deploy_dev.simple_pip.simple_pip.WINDOWS', layout == 'nt')
        site = abs_path(fs, site_rel)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            f'demo-1.0.data/{category}/{member}': b'demo data\n',
        })
        SimplePip(site).install(wheel)
        target = abs_path(fs, target_rel)
        assert os.path.isfile(target)
        with open(target, 'rb') as f:
            assert f.read() == b'demo data\n'
        assert (record_rel, sha256_record(b'demo data\n'), '10') in read_record(site)

    def test_install_data_categories_scheme(self, fs, pyc_compile):
        """The scheme of the environment is used, the layout is not re-derived."""
        site = site_packages(fs)
        scheme = Scheme(
            purelib=site,
            # A platform directory that the layout of site-packages can not tell
            platlib=abs_path(fs, '/env/lib64/site-packages'),
            scripts=abs_path(fs, '/env/Scripts'),
            data=abs_path(fs, '/env'),
            include=abs_path(fs, '/env/include/python3.8'),
        )
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo-1.0.data/purelib/demo/pure.py': b'pure\n',
            'demo-1.0.data/platlib/demo/plat.py': b'plat\n',
            'demo-1.0.data/scripts/demo-tool': b'#!python\n',
            'demo-1.0.data/data/share/demo/data.txt': b'demo data\n',
            'demo-1.0.data/headers/demo.h': b'/* demo */\n',
        })
        SimplePip(site, scheme=scheme).install(wheel)
        assert os.path.isfile(abs_path(fs, '/env/lib64/site-packages/demo/plat.py'))
        assert os.path.isfile(abs_path(fs, '/env/Scripts/demo-tool'))
        assert os.path.isfile(abs_path(fs, '/env/share/demo/data.txt'))
        assert os.path.isfile(abs_path(fs, '/env/include/python3.8/demo/demo.h'))
        assert sorted(path for path, _, _ in read_record(site)) == sorted([
            'demo-1.0.dist-info/INSTALLER',
            'demo-1.0.dist-info/METADATA',
            'demo-1.0.dist-info/RECORD',
            'demo-1.0.dist-info/WHEEL',
            'demo-1.0.dist-info/top_level.txt',
            'demo/pure.py',
            pyc_path('demo/pure.py'),
            '../../lib64/site-packages/demo/plat.py',
            pyc_path('../../lib64/site-packages/demo/plat.py'),
            '../../Scripts/demo-tool',
            '../../include/python3.8/demo/demo.h',
            '../../share/demo/data.txt',
        ])

    def test_install_data_unknown_category(self, fs, pyc_compile):
        """A .data directory that is not an install scheme key is rejected, like pip."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo-1.0.data/site-packages/demo/x.py': b'a = 1\n',
        })
        with pytest.raises(ValueError, match='unsupported .data directory "site-packages"'):
            SimplePip(site).install(wheel)
        assert list_files(site) == []

    @pytest.mark.parametrize("member", ['../evil.py', '/evil.py', 'demo/../../evil.py'])
    def test_install_rejects_traversal(self, fs, pyc_compile, member):
        """A wheel member outside of the install directory is rejected."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {member: b'evil\n'})
        with pytest.raises(ValueError, match='outside of the install directory'):
            SimplePip(site).install(wheel)
        assert list_files(abs_path(fs, '/env')) == []
        # The member is not written to any of the directories it could escape to
        for path in ['/evil.py', '/env/evil.py', '/env/Lib/evil.py', '/env/Lib/site-packages/evil.py']:
            assert not os.path.exists(abs_path(fs, path)), path

    def test_install_rejects_traversal_data(self, fs, pyc_compile):
        """A .data member escaping its scheme directory is rejected."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/1.0.data/scripts/../../evil.py'.replace('demo/1.0', 'demo-1.0'): b'evil\n',
        })
        with pytest.raises(ValueError, match='outside of the install directory'):
            SimplePip(site).install(wheel)
        assert list_files(abs_path(fs, '/env')) == []

    def test_install_wheel_without_dist_info(self, fs, pyc_compile):
        """PEP 427 requires the .dist-info directory."""
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {'demo/__init__.py': b''}, dist_info=False)
        with pytest.raises(ValueError, match='expected 1 .dist-info directory, got 0'):
            SimplePip(site_packages(fs)).install(wheel)

    def test_install_wheel_with_two_dist_info(self, fs, pyc_compile):
        """PEP 427 requires a single .dist-info directory."""
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'',
            'other-1.0.dist-info/METADATA': b'Metadata-Version: 2.1\n',
        })
        with pytest.raises(ValueError, match='expected 1 .dist-info directory, got 2'):
            SimplePip(site_packages(fs)).install(wheel)

    def test_install_wheel_without_metadata(self, fs, pyc_compile):
        """PEP 427 requires the METADATA of the .dist-info."""
        wheel = '/w/demo-1.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as zf:
            zf.writestr('demo-1.0.dist-info/WHEEL', b'Wheel-Version: 1.0\n')
        with pytest.raises(ValueError, match='missing .dist-info/METADATA'):
            SimplePip(site_packages(fs)).install(wheel)

    def test_install_wheel_with_invalid_dist_info(self, fs, pyc_compile):
        """The .dist-info directory is named "<name>-<version>.dist-info"."""
        wheel = '/w/demo-1.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as zf:
            zf.writestr('demo.dist-info/METADATA', b'Metadata-Version: 2.1\n')
        with pytest.raises(ValueError, match='invalid .dist-info directory "demo.dist-info"'):
            SimplePip(site_packages(fs)).install(wheel)

    def test_install_not_a_zip(self, fs, pyc_compile):
        """A file that is not a zip file."""
        fs.create_file('/w/demo-1.0-py3-none-any.whl', contents=b'not a zip file')
        with pytest.raises(zipfile.BadZipFile):
            SimplePip(site_packages(fs)).install('/w/demo-1.0-py3-none-any.whl')

    def test_install_missing_wheel(self, fs, pyc_compile):
        """A wheel that does not exist."""
        with pytest.raises(FileNotFoundError):
            SimplePip(site_packages(fs)).install('/w/demo-1.0-py3-none-any.whl')

    def test_uninstall(self, fs, pyc_compile):
        """Uninstalling removes every installed file."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo-1.0.data/data/share/demo/data.txt': b'demo data\n',
        })
        pip = SimplePip(site)
        pip.install(wheel)
        assert pip.uninstall('demo') is True
        assert list_files(abs_path(fs, '/env')) == []
        assert list_folders(site) == []

    def test_uninstall_missing_package(self, fs, capsys):
        """Uninstalling a package that is not installed."""
        site = site_packages(fs)
        assert SimplePip(site).uninstall('demo') is False
        assert capsys.readouterr().out == 'Package not exist: demo\n'

    def test_uninstall_prefix(self, fs, pyc_compile):
        """The prefix of SimplePip is used to remove the directories outside of site-packages."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo-1.0.data/data/share/demo/data.txt': b'demo data\n',
        })
        SimplePip(site).install(wheel)
        assert os.path.isfile(abs_path(fs, '/env/share/demo/data.txt'))
        pip = SimplePip(site, prefix=abs_path(fs, '/env'))
        assert pip.uninstall('demo') is True
        # The files are removed, the directories emptied under the prefix as well
        assert list_files(abs_path(fs, '/env')) == []
        assert list_folders(abs_path(fs, '/env')) == ['Lib', 'Lib/site-packages']

    def test_uninstall_prefix_bounds_folders(self, fs, pyc_compile):
        """A prefix narrower than the environment keeps the directories outside of it."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo-1.0.data/scripts/demo-tool': b'#!python\n',
        })
        SimplePip(site).install(wheel)
        pip = SimplePip(site, prefix=site)
        assert pip.uninstall('demo') is True
        assert list_files(abs_path(fs, '/env')) == []
        # <env>/Scripts is outside of the prefix, it is kept
        assert list_folders(abs_path(fs, '/env')) == ['Lib', 'Lib/site-packages', 'Scripts']


class TestPipConformance:
    """
    The installation has to be usable by pip.

    pip removes the files of the RECORD of a distribution and refuses to
    uninstall a distribution without one, see uninstallation_paths() and
    UninstallMissingRecord in pip/_internal/req/req_uninstall.py.
    """

    def test_record_covers_every_file(self, fs, pyc_compile):
        """Every installed file is recorded, so pip removes all of them."""
        site = site_packages(fs)
        env = abs_path(fs, '/env')
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo/sub/core.py': b'b = 2\n',
            'demo-1.0.data/scripts/demo-tool': b'#!python\n',
            'demo-1.0.data/data/share/demo/data.txt': b'demo data\n',
        })
        SimplePip(site).install(wheel)

        rows = read_record(site)
        recorded = {path for path, _, _ in rows}
        installed = set(list_files(env))
        # The paths of the RECORD are relative to site-packages, the files
        # installed outside of it are joined to their real location
        resolved = {
            to_posix(os.path.relpath(os.path.join(site, path), env))
            for path in recorded
        }
        assert resolved == installed
        assert sorted(recorded - set(list_files(site))) == [
            '../../Scripts/demo-tool',
            '../../share/demo/data.txt',
        ]

    def test_record_paths_are_relative(self, fs, pyc_compile):
        """PEP 376 requires the paths of a RECORD to be relative to site-packages."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo-1.0.data/scripts/demo-tool': b'#!python\n',
        })
        SimplePip(site).install(wheel)
        for path, _, _ in read_record(site):
            assert not os.path.isabs(path)
            assert not path.startswith('/')
            assert ':' not in path

    def test_record_checksum_format(self, fs, pyc_compile):
        """The checksums are "sha256=<urlsafe base64 without padding>", PEP 427."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {'demo/__init__.py': b'a = 1\n'})
        SimplePip(site).install(wheel)
        checksum = re.compile(r'^sha256=[A-Za-z0-9_-]+$')
        sizes = 0
        for path, sha256, size in read_record(site):
            if sha256:
                assert checksum.match(sha256), path
            else:
                # Only the files that can not be checked, e.g. a .pyc, have no checksum
                assert path.endswith('.pyc') or path.endswith('RECORD'), path
            if size:
                sizes += 1
                assert size.isdigit(), path
        assert sizes > 0

    def test_uninstall_removes_everything_installed(self, fs, pyc_compile):
        """Uninstalling removes every file of the RECORD, like pip."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {
            'demo/__init__.py': b'a = 1\n',
            'demo/sub/core.py': b'b = 2\n',
            'demo-1.0.data/scripts/demo-tool': b'#!python\n',
        })
        pip = SimplePip(site)
        pip.install(wheel)
        assert pip.uninstall('demo') is True
        assert list_files(abs_path(fs, '/env')) == []

    def test_dist_info_written_like_pip(self, fs, pyc_compile):
        """The files pip writes in the .dist-info of an installation."""
        site = site_packages(fs)
        wheel = build_wheel('/w/demo-1.0-py3-none-any.whl', {'demo/__init__.py': b'a = 1\n'})
        SimplePip(site).install(wheel)
        assert list_files(f'{site}/demo-1.0.dist-info') == [
            'INSTALLER',
            'METADATA',
            'RECORD',
            'WHEEL',
            'top_level.txt',
        ]
        dist = DistInfo(f'{site}/demo-1.0.dist-info')
        # The RECORD lists the distribution itself as well
        assert 'demo-1.0.dist-info/RECORD' in dist.record_list
