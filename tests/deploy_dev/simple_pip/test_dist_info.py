import os

import pytest
from conftest import abs_path, create_dist, list_files, list_folders, sha256_record, site_packages

from alasio.deploy_dev.simple_pip import DistInfo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401


class TestTopLevelList:
    def test_top_level_list(self, fs):
        """The import names of the distribution are read from top_level.txt."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b''})
        fs.create_file(f'{site}/demo-1.0.dist-info/top_level.txt', contents=b'demo\n')
        assert dist.top_level_list == ['demo']

    def test_top_level_list_missing(self, fs):
        """A distribution built by a backend not writing top_level.txt."""
        dist = create_dist(fs, {'demo/__init__.py': b''})
        assert dist.top_level_list == []

    def test_top_level_list_normalized(self, fs):
        """Blank lines and the backslashes of the rows are normalized."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b''})
        content = 'adodbapi\n\nwin32\\lib\\afxres\n'
        fs.create_file(f'{site}/demo-1.0.dist-info/top_level.txt', contents=content)
        assert dist.top_level_list == ['adodbapi', 'win32/lib/afxres']


class TestRecordList:
    def test_record_list(self, fs):
        """The RECORD of the distribution is read, the checksum is kept."""
        dist = create_dist(fs, {'demo/__init__.py': b'a = 1\n'})
        entries = dist.record_list
        assert list(entries) == ['demo/__init__.py', 'demo-1.0.dist-info/METADATA', 'demo-1.0.dist-info/RECORD']
        assert (entries['demo/__init__.py'].sha256, entries['demo/__init__.py'].size) == (
            sha256_record(b'a = 1\n'), '6')

    def test_record_list_missing(self, fs):
        """A distribution without RECORD, pip refuses to uninstall it as well."""
        dist = create_dist(fs, {'demo/__init__.py': b''}, record=False)
        assert dist.record_list == {}

    def test_record_list_empty(self, fs):
        dist = create_dist(fs, {'demo/__init__.py': b''}, record=[])
        assert dist.record_list == {}


class TestResolvePath:
    def test_resolve_path(self, fs):
        """The paths of a RECORD are relative to site-packages, PEP 376."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b''})
        assert dist.resolve_path('demo/__init__.py') == f'{site}/demo/__init__.py'

    def test_resolve_path_outside_site_packages(self, fs):
        """A file installed outside of site-packages is recorded with "../.."."""
        dist = create_dist(fs, {'demo/__init__.py': b'', '../../Scripts/demo-tool': b''})
        assert dist.resolve_path('../../Scripts/demo-tool') == abs_path(fs, '/env/Scripts/demo-tool')

    def test_resolve_path_windows_record(self, fs):
        """A RECORD written on Windows holds backslashes."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b''})
        assert dist.resolve_path('demo\\sub\\core.py') == f'{site}/demo/sub/core.py'


class TestRecordPaths:
    def test_record_paths(self, fs):
        """The recorded paths are resolved, the entries are not checked on disk."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b'a = 1\n'}, record=[
            ('demo/__init__.py', sha256_record(b'a = 1\n'), '6'),
            ('demo/gone.py', sha256_record(b''), '0'),
            ('demo-1.0.dist-info/METADATA', sha256_record(b''), '0'),
            ('demo-1.0.dist-info/RECORD', '', ''),
        ])
        assert {str(path): str(file) for path, file in dist.record_paths.items()} == {
            'demo/__init__.py': f'{site}/demo/__init__.py',
            'demo/gone.py': f'{site}/demo/gone.py',
            'demo-1.0.dist-info/METADATA': f'{site}/demo-1.0.dist-info/METADATA',
            'demo-1.0.dist-info/RECORD': f'{site}/demo-1.0.dist-info/RECORD',
        }

    def test_record_paths_directory(self, fs):
        """A recorded path that is a directory on disk is resolved like a file."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/sub/core.py': b''}, record=[
            ('demo/sub', sha256_record(b''), '0'),
            ('demo/sub/core.py', sha256_record(b''), '0'),
        ])
        assert {str(path): str(file) for path, file in dist.record_paths.items()} == {
            'demo/sub': f'{site}/demo/sub',
            'demo/sub/core.py': f'{site}/demo/sub/core.py',
        }


class TestFolderToDelete:
    def test_folder_to_delete(self, fs):
        """The directories holding a recorded file are listed, site-packages is not."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b'', 'demo/core.py': b''})
        assert sorted(str(folder) for folder in dist.folder_to_delete) == sorted([
            f'{site}/demo', f'{site}/demo-1.0.dist-info'])
        assert site not in [str(folder) for folder in dist.folder_to_delete]

    def test_folder_to_delete_deepest_first(self, fs):
        """A directory is removed after its subdirectories, os.rmdir() needs them empty."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/sub/deep/core.py': b''})
        folders = [str(folder) for folder in dist.folder_to_delete]
        assert sorted(folders) == sorted([
            f'{site}/demo/sub/deep', f'{site}/demo/sub', f'{site}/demo', f'{site}/demo-1.0.dist-info'])
        assert folders.index(f'{site}/demo/sub/deep') < folders.index(f'{site}/demo/sub')
        assert folders.index(f'{site}/demo/sub') < folders.index(f'{site}/demo')

    def test_folder_outside_site_packages(self, fs):
        """The directories holding the files installed outside of site-packages are listed."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b'', '../../share/demo/data.txt': b''})
        assert sorted(str(folder) for folder in dist.folder_to_delete) == sorted([
            f'{site}/demo', f'{site}/demo-1.0.dist-info',
            abs_path(fs, '/env/share/demo'), abs_path(fs, '/env/share'),
        ])

    def test_folder_never_site_packages_or_prefix(self, fs):
        """site-packages and the root of the environment are never listed."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b'', '../../Scripts/demo-tool': b''})
        folders = [str(folder) for folder in dist.folder_to_delete]
        assert abs_path(fs, '/env/Scripts') in folders
        assert site not in folders
        assert abs_path(fs, '/env') not in folders

    def test_folder_outside_site_packages_shared(self, fs):
        """A directory shared with an other distribution is listed as well.

        The listing does not scan the disk, the directory is simply not
        removed by uninstall() when it is not empty.
        """
        dist = create_dist(fs, {'demo/__init__.py': b'', '../../Scripts/demo-tool': b''})
        fs.create_file(abs_path(fs, '/env/Scripts/other-tool'), contents=b'')
        assert abs_path(fs, '/env/Scripts') in [str(folder) for folder in dist.folder_to_delete]


class TestPrefix:
    def test_prefix_derived(self, fs):
        """Without a prefix of the caller, it is derived from the layout of site-packages."""
        site_rel = '/env/Lib/site-packages' if os.name == 'nt' else '/env/lib/python3.8/site-packages'
        site = abs_path(fs, site_rel)
        dist = create_dist(fs, {'demo/__init__.py': b''}, site=site)
        assert dist.prefix == abs_path(fs, '/env')

    def test_prefix_given(self, fs):
        """The prefix of the caller bounds the directories to remove."""
        site = site_packages(fs)
        create_dist(fs, {'demo/__init__.py': b'', '../../Scripts/demo-tool': b''})
        dist = DistInfo(f'{site}/demo-1.0.dist-info', prefix=site)
        assert dist.prefix == site
        # The directories outside of the prefix are not listed
        assert sorted(str(folder) for folder in dist.folder_to_delete) == sorted([
            f'{site}/demo', f'{site}/demo-1.0.dist-info'])


class TestRecordOrder:
    """
    The order of the rows of a RECORD carries no meaning, PEP 376.

    pip reads the RECORD into a set of paths (UninstallPathSet._paths) and
    sorts the paths it removes itself, the order of the file changes
    nothing. The paths of the test are the ones where the sort of the raw
    strings disagrees with the tree: "/" is 0x2f, before the digits and the
    letters but after "-" and ".", so "demo/aaa/b.py" sorts before
    "demo/aaaname.py" and after "demo/aaa-z.py".
    """

    FILES = {
        'demo/aaaname.py': b'aname = 1\n',
        'demo/aaa/b.py': b'b = 2\n',
        'demo/bbbname.py': b'bname = 3\n',
        'demo/aaa-z.py': b'z = 4\n',
        '../../Scripts/demo-tool': b'#!python\n',
    }

    @pytest.mark.parametrize("order", [
        # The order the files are written to the wheel
        ['demo/aaaname.py', 'demo/aaa/b.py', 'demo/bbbname.py', 'demo/aaa-z.py'],
        # Reverse of the sorted paths, the order RecordManager.dump_bytes() writes
        ['demo/bbbname.py', 'demo/aaaname.py', 'demo/aaa-z.py', 'demo/aaa/b.py'],
        # A subdirectory first, then the files of the top level
        ['demo/aaa/b.py', 'demo/aaa-z.py', 'demo/aaaname.py', 'demo/bbbname.py'],
    ])
    def test_uninstall_any_order(self, fs, order):
        """The uninstallation removes the same files whatever the order of the rows is."""
        site = site_packages(fs)
        rows = [
            (path, sha256_record(self.FILES[path]), str(len(self.FILES[path])))
            for path in order
        ]
        rows += [
            ('../../Scripts/demo-tool', sha256_record(self.FILES['../../Scripts/demo-tool']),
             str(len(self.FILES['../../Scripts/demo-tool']))),
            ('demo-1.0.dist-info/METADATA', sha256_record(b''), '0'),
            ('demo-1.0.dist-info/RECORD', '', ''),
        ]
        dist = create_dist(fs, self.FILES, record=rows)
        # The uninstallation removes the same files whatever the order is, the
        # shared directory of the tool is not empty after the removal
        assert dist.uninstall() == (7, 4)
        assert list_files(abs_path(fs, '/env')) == []
        assert list_folders(abs_path(fs, '/env')) == ['Lib', 'Lib/site-packages']


class TestUninstall:
    def test_uninstall(self, fs):
        """Every recorded file is removed, the number of files and folders removed is returned."""
        site = site_packages(fs)
        dist = create_dist(fs, {
            'demo/__init__.py': b'a = 1\n',
            'demo/sub/core.py': b'b = 2\n',
            '../../Scripts/demo-tool': b'#!python\n',
            '../../share/demo/data.txt': b'demo data\n',
        })
        assert dist.uninstall() == (6, 6)
        assert list_files(abs_path(fs, '/env')) == []
        assert list_folders(site) == []
        # site-packages and the directories above it are kept
        assert list_folders(abs_path(fs, '/env')) == ['Lib', 'Lib/site-packages']

    def test_uninstall_keeps_other_distribution(self, fs):
        """The files of an other distribution sharing a directory are kept."""
        site = site_packages(fs)
        dist = create_dist(fs, {'shared/demo.py': b'a = 1\n'}, name='nsa')
        create_dist(fs, {'shared/other.py': b'a = 2\n'}, name='nsb')
        # The shared directory is not empty, os.rmdir() leaves it
        assert dist.uninstall() == (3, 1)
        assert list_files(site) == [
            'nsb-1.0.dist-info/METADATA',
            'nsb-1.0.dist-info/RECORD',
            'shared/other.py',
        ]
        assert list_folders(site) == ['nsb-1.0.dist-info', 'shared']

    def test_uninstall_keeps_unrecorded_file(self, fs):
        """A file created after the installation is kept, with its directories."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b'a = 1\n'})
        fs.create_file(f'{site}/demo/stale.cpython-39.pyc', contents=b'')
        assert dist.uninstall() == (3, 1)
        assert list_files(site) == ['demo/stale.cpython-39.pyc']
        assert list_folders(site) == ['demo']

    def test_uninstall_missing_file(self, fs):
        """A recorded file removed by hand is skipped, the rest is removed."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b'', 'demo/core.py': b''})
        os.remove(f'{site}/demo/core.py')
        assert dist.uninstall() == (3, 2)
        assert list_files(site) == []

    def test_uninstall_directory_entry(self, fs):
        """A RECORD row that is a directory is left to the directory removal."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/sub/core.py': b''}, record=[
            ('demo/sub', sha256_record(b''), '0'),
            ('demo/sub/core.py', sha256_record(b''), '0'),
            ('demo-1.0.dist-info/METADATA', sha256_record(b''), '0'),
            ('demo-1.0.dist-info/RECORD', '', ''),
        ])
        assert dist.uninstall() == (3, 3)
        assert list_files(site) == []

    def test_uninstall_without_record(self, fs):
        """A distribution without RECORD is kept, pip refuses to uninstall it as well."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b''}, record=False)
        with logger.mock_capture_writer() as capture:
            assert dist.uninstall() is None
        assert [(log['l'], log['m']) for log in capture.backend.logs] == [
            ('WARNING', 'Cannot uninstall demo: no RECORD file, unknown files of the package')]
        assert capture.fd.any_contains('no RECORD file, unknown files of the package')
        assert list_files(site) == ['demo-1.0.dist-info/METADATA', 'demo/__init__.py']

    def test_uninstall_with_empty_record(self, fs):
        """A RECORD without any row removes nothing, like pip reports."""
        site = site_packages(fs)
        dist = create_dist(fs, {'demo/__init__.py': b''}, record=[])
        with logger.mock_capture_writer() as capture:
            assert dist.uninstall() is None
        assert [(log['l'], log['m']) for log in capture.backend.logs] == [
            ('WARNING', 'Cannot uninstall demo: the RECORD lists no file')]
        assert list_files(site) == [
            'demo-1.0.dist-info/METADATA', 'demo-1.0.dist-info/RECORD', 'demo/__init__.py']
