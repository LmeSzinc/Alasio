import builtins
import os

import pytest

from alasio.deploy.simple_pip import pip_list
from alasio.deploy.simple_pip.pip_list import Distribution, PipList, normalize_name
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import IS_WINDOWS
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401

# Root of the tests, a site-packages look-alike
SITE = '/env/Lib/site-packages'


def metadata(name, version, extra=''):
    """
    Content of a METADATA or PKG-INFO file.

    Args:
        name (str): Name header of the metadata
        version (str): Version header of the metadata
        extra (str): Extra header lines, each ends with a newline.
            Defaults to ''

    Returns:
        str: Content of the file: the headers, the empty line that ends
            them and a description body
    """
    return (
        f'Metadata-Version: 2.1\n'
        f'Name: {name}\n'
        f'Version: {version}\n'
        f'{extra}'
        f'\n'
        f'The description of {name}.\n'
    )


def create_dist_info(fake, folder, content=None):
    """
    Create a .dist-info directory.

    Args:
        fake (FakeFilesystem): Active fake filesystem
        folder (str): Name of the directory, e.g. "demo-1.0.dist-info"
        content (str | None): Content of the METADATA file. Defaults to
            None, the directory is created empty

    Returns:
        str: Path of the directory
    """
    path = f'{SITE}/{folder}'
    if content is None:
        fake.create_dir(path)
    else:
        fake.create_file(f'{path}/METADATA', contents=content)
    return path


def create_egg_info(fake, folder, content=None):
    """
    Create a .egg-info directory.

    Args:
        fake (FakeFilesystem): Active fake filesystem
        folder (str): Name of the directory, e.g. "alasio.egg-info"
        content (str | None): Content of the PKG-INFO file. Defaults to
            None, the directory is created empty

    Returns:
        str: Path of the directory
    """
    path = f'{SITE}/{folder}'
    if content is None:
        fake.create_dir(path)
    else:
        fake.create_file(f'{path}/PKG-INFO', contents=content)
    return path


def run_list(root=SITE):
    """
    List the distributions of a site-packages directory.

    Args:
        root (str): Path of the directory. Defaults to SITE

    Returns:
        list[Distribution]: The distributions, in the order of the listing
    """
    return PipList(root).list()


def names(dists):
    """
    (name, version) pairs of a listing, the compact form of an assert.

    Args:
        dists (list[Distribution]): Listing of a directory

    Returns:
        list[tuple[str, str]]: The pairs, in the order of the listing
    """
    return [(dist.name, dist.version) for dist in dists]


class TestNormalizeName:
    @pytest.mark.parametrize('name, expected', [
        ('ruamel.yaml', 'ruamel-yaml'),
        ('ruamel_yaml', 'ruamel-yaml'),
        ('Ruamel.Yaml', 'ruamel-yaml'),
        ('zope.interface', 'zope-interface'),
        ('importlib_metadata', 'importlib-metadata'),
        ('Django', 'django'),
        ('demo', 'demo'),
        ('a..b--c__d', 'a-b-c-d'),
        ('-demo-', '-demo-'),
        ('', ''),
    ])
    def test_normalize(self, name, expected):
        """Every run of "-", "_" and "." is a single "-" and the name is lower cased."""
        assert normalize_name(name) == expected


class TestDistribution:
    def test_dist_key(self):
        """The normalized name finds a distribution of any separator style."""
        dist = Distribution('ruamel.yaml', '0.18.6', PathStr('/env/ruamel_yaml-0.18.6.dist-info'))
        assert dist.dist_key == 'ruamel-yaml'
        assert Distribution('Ruamel.Yaml', '0.18.6', '').dist_key == 'ruamel-yaml'

    def test_repr(self):
        """The representation names the fields of the distribution."""
        dist = Distribution('demo', '1.0', PathStr('/env/demo-1.0.dist-info'))
        assert repr(dist) == "Distribution('demo', '1.0', '/env/demo-1.0.dist-info')"


class TestInit:
    def test_normalize_root(self):
        """The directory path is normalized: a trailing separator is stripped."""
        assert PipList(SITE).site_packages == SITE
        assert PipList(f'{SITE}/').site_packages == SITE

    @pytest.mark.skipif(not IS_WINDOWS, reason='A Windows path is normalized on Windows only')
    def test_normalize_root_windows(self):
        """The backslashes of a Windows path are normalized to "/"."""
        assert PipList(SITE.replace('/', '\\')).site_packages == SITE


class TestListEmpty:
    def test_empty_directory(self, fs):
        """A site-packages without an installation holds no distribution."""
        fs.create_dir(SITE)
        assert run_list() == []

    def test_missing_directory(self, fs):
        """The directory of an environment that was never created holds no distribution."""
        assert run_list() == []

    def test_not_a_directory(self, fs):
        """A path that is a file holds no distribution."""
        fs.create_file(SITE, contents='')
        assert run_list() == []


class TestDistInfo:
    def test_metadata_names_the_distribution(self, fs):
        """The Name and the Version headers of the METADATA are the distribution."""
        folder = create_dist_info(fs, 'ruamel_yaml-0.18.6.dist-info', metadata('ruamel.yaml', '0.18.6'))
        result = run_list()
        assert names(result) == [('ruamel.yaml', '0.18.6')]
        assert result[0].info == folder
        assert isinstance(result[0].info, PathStr)
        assert result[0].dist_key == 'ruamel-yaml'

    def test_directory_name_is_the_fallback(self, fs):
        """A .dist-info directory without a METADATA file still names the distribution."""
        create_dist_info(fs, 'ruamel_yaml-0.18.6.dist-info')
        result = run_list()
        assert names(result) == [('ruamel_yaml', '0.18.6')]
        # the name of the directory is escaped, PEP 427, the normalized key
        # finds the distribution as it is named in a config
        assert result[0].dist_key == 'ruamel-yaml'

    def test_metadata_without_version(self, fs):
        """A metadata without a Version header falls back to the version of the directory name."""
        create_dist_info(fs, 'demo-1.0.dist-info', 'Metadata-Version: 2.1\nName: demo\n\n')
        assert names(run_list()) == [('demo', '1.0')]

    def test_metadata_without_name(self, fs):
        """A metadata without a Name header falls back to the name of the directory."""
        create_dist_info(fs, 'ruamel_yaml-0.18.6.dist-info', 'Metadata-Version: 2.1\nVersion: 0.18.6\n\n')
        assert names(run_list()) == [('ruamel_yaml', '0.18.6')]

    def test_header_case(self, fs):
        """The header names are not case sensitive, the value keeps its case."""
        create_dist_info(fs, 'demo-1.0.dist-info', 'metadata-version: 2.1\nname: Demo\nversion: 1.0\n\n')
        assert names(run_list()) == [('Demo', '1.0')]

    def test_metadata_directory(self, fs):
        """A METADATA entry that is a directory is not read, the directory name is the fallback."""
        create_dist_info(fs, 'demo-1.0.dist-info')
        fs.create_dir(f'{SITE}/demo-1.0.dist-info/METADATA')
        assert names(run_list()) == [('demo', '1.0')]

    def test_unnamed_entry_skipped(self, fs):
        """A metadata entry that names no distribution is skipped with a warning."""
        create_dist_info(fs, 'broken.dist-info', 'Metadata-Version: 2.1\n\n')
        with logger.mock_capture_writer() as capture:
            assert run_list() == []
        assert capture.fd.any_contains(f'Skipped a metadata entry that names no distribution: {SITE}/broken.dist-info')

    def test_dist_info_file(self, fs):
        """A file named *.dist-info is not an installation, the metadata of one is a directory."""
        fs.create_file(f'{SITE}/demo-1.0.dist-info', contents=metadata('demo', '1.0'))
        assert run_list() == []

    def test_not_recursive(self, fs):
        """Only the top level of the directory is read, where the installations are."""
        fs.create_file(f'{SITE}/sub/demo-1.0.dist-info/METADATA', contents=metadata('demo', '1.0'))
        assert run_list() == []

    def test_unknown_entries_ignored(self, fs):
        """Entries that are not metadata directories or files are not distributions."""
        fs.create_file(f'{SITE}/demo.py', contents='')
        fs.create_file(f'{SITE}/demo-1.0.dist-info.bak', contents=metadata('demo', '1.0'))
        fs.create_file(f'{SITE}/demo-1.0.egg-info.bak', contents=metadata('demo', '1.0'))
        fs.create_dir(f'{SITE}/__pycache__')
        fs.create_dir(f'{SITE}/demo.libs')
        assert run_list() == []


class TestEggInfo:
    def test_directory(self, fs):
        """A .egg-info directory is listed with the Name and Version of its PKG-INFO."""
        folder = create_egg_info(fs, 'alasio.egg-info', metadata('alasio', '0.0.1'))
        result = run_list()
        assert names(result) == [('alasio', '0.0.1')]
        assert result[0].info == folder

    def test_directory_without_pkg_info(self, fs):
        """A .egg-info directory without a PKG-INFO names the distribution only."""
        create_egg_info(fs, 'alasio.egg-info')
        assert names(run_list()) == [('alasio', '')]

    def test_file(self, fs):
        """A .egg-info file holds the metadata itself."""
        fs.create_file(f'{SITE}/alasio.egg-info', contents=metadata('alasio', '0.0.1'))
        result = run_list()
        assert names(result) == [('alasio', '0.0.1')]
        assert result[0].info == f'{SITE}/alasio.egg-info'

    def test_file_name_with_version(self, fs):
        """The legacy name carries no version the listing reads, the version comes from the metadata."""
        fs.create_file(f'{SITE}/alasio-0.0.1-py3.8.egg-info', contents=metadata('alasio', '0.0.1'))
        result = run_list()
        assert names(result) == [('alasio', '0.0.1')]
        assert result[0].info == f'{SITE}/alasio-0.0.1-py3.8.egg-info'

    def test_egg_link_not_read(self, fs):
        """An .egg-link points to a tree outside of the directory, it is not an installation of it."""
        fs.create_file(f'{SITE}/alasio.egg-link', contents='/elsewhere/alasio\n/elsewhere\n')
        fs.create_file('/elsewhere/alasio.egg-info/PKG-INFO', contents=metadata('alasio', '0.0.1'))
        assert run_list() == []

    def test_egg_directory_not_read(self, fs):
        """An .egg directory is an installation of easy_install, not an entry of setuptools or pip."""
        fs.create_file(f'{SITE}/alasio-0.0.1-py3.8.egg/EGG-INFO/PKG-INFO', contents=metadata('alasio', '0.0.1'))
        assert run_list() == []


class TestOrder:
    def test_sorted_by_normalized_name(self, fs):
        """The listing is sorted by the normalized name, the case and the separators do not decide it."""
        create_egg_info(fs, 'Zope.egg-info', metadata('Zope', '5.8.6'))
        create_dist_info(fs, 'ruamel_yaml-0.18.6.dist-info', metadata('ruamel.yaml', '0.18.6'))
        create_dist_info(fs, 'alasio-0.0.1.dist-info', metadata('alasio', '0.0.1'))
        assert names(run_list()) == [('alasio', '0.0.1'), ('ruamel.yaml', '0.18.6'), ('Zope', '5.8.6')]

    def test_the_same_distribution_twice(self, fs):
        """Every metadata entry is listed, a stale .dist-info of an old version included."""
        create_dist_info(fs, 'demo-2.0.dist-info', metadata('demo', '2.0'))
        create_dist_info(fs, 'demo-1.0.dist-info', metadata('demo', '1.0'))
        assert names(run_list()) == [('demo', '1.0'), ('demo', '2.0')]

    def test_order_independent_of_the_directory(self, fs):
        """The listing does not depend on the order the directory returns its entries in."""
        fs.create_file('/env/one/demo-1.0.dist-info/METADATA', contents=metadata('demo', '1.0'))
        fs.create_file('/env/one/alasio-0.0.1.dist-info/METADATA', contents=metadata('alasio', '0.0.1'))
        fs.create_file('/env/two/alasio-0.0.1.dist-info/METADATA', contents=metadata('alasio', '0.0.1'))
        fs.create_file('/env/two/demo-1.0.dist-info/METADATA', contents=metadata('demo', '1.0'))
        assert names(run_list('/env/one')) == [('alasio', '0.0.1'), ('demo', '1.0')]
        assert names(run_list('/env/two')) == [('alasio', '0.0.1'), ('demo', '1.0')]


class TestGet:
    def test_found(self, fs):
        """The name is compared normalized, the style of the caller does not matter."""
        create_dist_info(fs, 'ruamel_yaml-0.18.6.dist-info', metadata('ruamel.yaml', '0.18.6'))
        dist = PipList(SITE).get('Ruamel.Yaml')
        assert dist is not None
        assert names([dist]) == [('ruamel.yaml', '0.18.6')]

    def test_not_installed(self, fs):
        """A name the directory holds no distribution of gives None."""
        create_dist_info(fs, 'demo-1.0.dist-info', metadata('demo', '1.0'))
        assert PipList(SITE).get('alasio') is None

    def test_first_of_the_same_name(self, fs):
        """The distribution of the first entry in the order of list() is returned."""
        create_dist_info(fs, 'demo-2.0.dist-info', metadata('demo', '2.0'))
        create_dist_info(fs, 'demo-1.0.dist-info', metadata('demo', '1.0'))
        assert PipList(SITE).get('demo').version == '1.0'

    def test_legacy(self, fs):
        """A legacy installation is found the same way."""
        create_egg_info(fs, 'alasio.egg-info', metadata('alasio', '0.0.1'))
        assert PipList(SITE).get('alasio').version == '0.0.1'


class TestFreshListing:
    def test_installation_visible(self, fs):
        """The directory is read on every call, an installation in between is listed."""
        site = PipList(SITE)
        assert site.list() == []
        create_dist_info(fs, 'demo-1.0.dist-info', metadata('demo', '1.0'))
        assert names(site.list()) == [('demo', '1.0')]


class TestSymlinks:
    def test_symlinked_dist_info_not_read(self, fs):
        """A .dist-info directory that is a symbolic link is not an installation of the directory."""
        fs.create_file('/env/other/demo-1.0.dist-info/METADATA', contents=metadata('demo', '1.0'))
        fs.create_symlink(f'{SITE}/demo-1.0.dist-info', '/env/other/demo-1.0.dist-info')
        assert run_list() == []

    def test_symlinked_egg_info_not_read(self, fs):
        """A .egg-info file that is a symbolic link is not read."""
        fs.create_file('/env/other/demo.egg-info', contents=metadata('demo', '1.0'))
        fs.create_symlink(f'{SITE}/demo.egg-info', '/env/other/demo.egg-info')
        assert run_list() == []


class TestMinimalIo:
    def test_reads_the_directory_once(self, fs, monkeypatch):
        """The directory is listed once, no entry is read twice."""
        create_dist_info(fs, 'demo-1.0.dist-info', metadata('demo', '1.0'))
        create_egg_info(fs, 'alasio.egg-info', metadata('alasio', '0.0.1'))
        scandir = os.scandir
        calls = []

        def scandir_record(path):
            calls.append(path)
            return scandir(path)

        monkeypatch.setattr(os, 'scandir', scandir_record)
        assert len(run_list()) == 2
        assert calls == [SITE]

    def test_opens_the_metadata_only(self, fs, monkeypatch):
        """Only the metadata files are read, a file that is not one is never opened."""
        create_dist_info(fs, 'demo-1.0.dist-info', metadata('demo', '1.0'))
        fs.create_file(f'{SITE}/demo-1.0.dist-info/RECORD', contents='')
        fs.create_file(f'{SITE}/demo.py', contents='')
        opened = []
        real_open = builtins.open

        def open_record(file, *args, **kwargs):
            if isinstance(file, str) and file.startswith(SITE):
                opened.append(file)
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, 'open', open_record)
        assert names(run_list()) == [('demo', '1.0')]
        assert opened == [f'{SITE}/demo-1.0.dist-info/METADATA']


class TestLogging:
    def test_silent_on_a_healthy_directory(self, fs):
        """A listing of a healthy directory logs nothing."""
        create_dist_info(fs, 'demo-1.0.dist-info', metadata('demo', '1.0'))
        with logger.mock_capture_writer() as capture:
            assert len(run_list()) == 1
        assert capture.fd.logs == []
        assert capture.backend.logs == []


class TestReadHeaders:
    def test_headers(self, fs):
        """The headers are read as a lower-cased name -> value mapping."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents=metadata('demo', '1.0', 'Summary: a demo\nAuthor: someone\n'))
        assert pip_list._read_headers(file) == {
            'metadata-version': '2.1',
            'name': 'demo',
            'version': '1.0',
            'summary': 'a demo',
            'author': 'someone',
        }

    def test_body_not_read(self, fs):
        """The read stops at the empty line that ends the headers, RFC 822."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents='Name: demo\nVersion: 1.0\n\nName: other\nVersion: 2.0\n\n')
        assert pip_list._read_headers(file) == {'name': 'demo', 'version': '1.0'}

    def test_folded_value(self, fs):
        """A continuation line is joined to the value of the header above, RFC 822."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents='Name: demo\nX-Long: part 1\n\tpart 2\nX-Empty:\n part 3\n\n')
        assert pip_list._read_headers(file) == {
            'name': 'demo',
            'x-long': 'part 1 part 2',
            'x-empty': 'part 3',
        }

    def test_duplicate_header(self, fs):
        """The first occurrence of a header wins, like the email parser of the standard library."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents='Name: first\nName: second\nVersion: 1.0\n\n')
        assert pip_list._read_headers(file) == {'name': 'first', 'version': '1.0'}

    def test_malformed_lines(self, fs):
        """A line that holds no header is skipped, the headers around it are read."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents='not a header\nName: demo\n: no name\nVersion: 1.0\n\n')
        assert pip_list._read_headers(file) == {'name': 'demo', 'version': '1.0'}

    def test_no_blank_line(self, fs):
        """A file that ends without the empty line is read to its end."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents='Name: demo\nVersion: 1.0')
        assert pip_list._read_headers(file) == {'name': 'demo', 'version': '1.0'}

    def test_crlf(self, fs):
        """The line endings of a file written on Windows are read."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents='Name: demo\r\nVersion: 1.0\r\n\r\nbody\r\n')
        assert pip_list._read_headers(file) == {'name': 'demo', 'version': '1.0'}

    def test_byte_order_mark(self, fs):
        """A byte order mark of a file written by a tool is tolerated."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents=b'\xef\xbb\xbfName: demo\nVersion: 1.0\n\n')
        assert pip_list._read_headers(file) == {'name': 'demo', 'version': '1.0'}

    def test_invalid_utf8(self, fs):
        """A byte that is not UTF-8 is replaced, the headers around it are read."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents=b'Name: demo\nVersion: 1.0\nSummary: \xff\xfe\n\n')
        assert pip_list._read_headers(file) == {'name': 'demo', 'version': '1.0', 'summary': '\ufffd\ufffd'}

    def test_empty_file(self, fs):
        """A file that holds nothing holds no header."""
        file = f'{SITE}/demo-1.0.dist-info/METADATA'
        fs.create_file(file, contents='')
        assert pip_list._read_headers(file) == {}

    def test_missing_file(self, fs):
        """A file that does not exist holds no header."""
        assert pip_list._read_headers(f'{SITE}/missing-1.0.dist-info/METADATA') == {}

    def test_directory(self, fs):
        """A path that is a directory holds no header."""
        fs.create_dir(f'{SITE}/demo-1.0.dist-info/METADATA')
        assert pip_list._read_headers(f'{SITE}/demo-1.0.dist-info/METADATA') == {}


class TestDependencies:
    def test_no_importlib(self):
        """The metadata files are read as text, importlib.metadata is not imported."""
        assert 'importlib' not in vars(pip_list)
        assert 'pkg_resources' not in vars(pip_list)

    def test_no_pip(self):
        """No pip of any kind is imported nor run."""
        assert 'pip' not in vars(pip_list)
