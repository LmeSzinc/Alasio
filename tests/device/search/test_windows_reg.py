import codecs

import pytest

from alasio.ext.env import WINDOWS

# windows_reg.py imports winreg at module level, which does not exist on posix
if WINDOWS:
    from alasio.device.search import windows_reg
    from alasio.device.search.windows_reg import (
        extract_uninstall_path,
        get_install_dir_from_reg,
        iter_known_registry,
        iter_mui_cache,
        iter_uninstall_registry,
        iter_user_assist,
        list_key,
        list_reg,
    )

from tests.device.search._registry_stub import FakeWinreg  # noqa: E402

pytestmark = [
    pytest.mark.parallel,
    pytest.mark.skipif(not WINDOWS, reason='The Windows registry is not available on this platform'),
]


class TestExtractUninstallPath:
    def test_empty_and_invalid(self):
        """
        Test invalid or empty inputs
        """
        assert extract_uninstall_path('') == ''
        assert extract_uninstall_path('   ') == ''
        # Non-string inputs
        assert extract_uninstall_path(None) == ''
        assert extract_uninstall_path(123) == ''
        assert extract_uninstall_path([]) == ''

    def test_quoted_paths(self):
        """
        Test paths wrapped in double quotes
        """
        # documentation examples
        assert extract_uninstall_path(
            '"C:\\Program Files (x86)\\Everything/Uninstall.exe"'
        ) == 'C:\\Program Files (x86)\\Everything/Uninstall.exe'
        assert extract_uninstall_path(
            '"E:\\ProgramFiles\\Microvirt\\MEmu/uninstall/uninstall.exe" -u'
        ) == 'E:\\ProgramFiles\\Microvirt\\MEmu/uninstall/uninstall.exe'

        # Incomplete quotes or weird quoting
        assert extract_uninstall_path('"C:\\test.exe') == 'C:\\test.exe'
        assert extract_uninstall_path('""C:\\test.exe""') == ''  # First " initiates, second " terminates
        assert extract_uninstall_path('"C:\\Program Files" /S') == 'C:\\Program Files'

    def test_unquoted_paths_without_space(self):
        """
        Test simple unquoted paths without spaces
        """
        assert extract_uninstall_path(
            'E:\\ProgramFiles\\AweSun\\AweSun.exe'
        ) == 'E:\\ProgramFiles\\AweSun\\AweSun.exe'
        assert extract_uninstall_path(
            'C:\\Windows\\aaa.EXE'
        ) == 'C:\\Windows\\aaa.EXE'
        assert extract_uninstall_path(
            'C:/Windows/system32/cmd.exe'
        ) == 'C:/Windows/system32/cmd.exe'

    def test_unquoted_paths_with_space(self):
        """
        Test unquoted paths with spaces and optional arguments
        """
        # documentation examples
        assert extract_uninstall_path(
            'C:\\Program Files\\BlueStacks_nxt\\BlueStacksUninstaller.exe -tmp'
        ) == 'C:\\Program Files\\BlueStacks_nxt\\BlueStacksUninstaller.exe'
        assert extract_uninstall_path(
            'E:\\ProgramFiles\\PyCharm Community Edition 2023.3.7\\bin/Uninstall.exe'
        ) == 'E:\\ProgramFiles\\PyCharm Community Edition 2023.3.7\\bin/Uninstall.exe'
        assert extract_uninstall_path(
            'E:\\ProgramFiles\\Revit 2020\\Setup\\Setup.exe /P {xxx} /M RVT /LANG zh-CN'
        ) == 'E:\\ProgramFiles\\Revit 2020\\Setup\\Setup.exe'

        # extension in arguments should not confuse the detection of the first main extension
        assert extract_uninstall_path(
            'C:\\Windows\\aaa.EXE -uninstall=\\"D:/xxx/xxx.exe\\"'
        ) == 'C:\\Windows\\aaa.EXE'

        # tricky: path with space but NO extension matched in the middle
        # If no extension is found, it falls back to the whole string
        assert extract_uninstall_path(
            'C:\\My App\\Uninstall'
        ) == 'C:\\My App\\Uninstall'

    def test_extensions(self):
        """
        Test all valid extensions used by the function
        """
        # valid_ext = ('.exe', '.msi', '.bat', '.com', '.cmd')
        for ext in ['.exe', '.msi', '.bat', '.com', '.cmd']:
            # Case insensitive check
            assert extract_uninstall_path(
                f'C:\\test{ext} /arg'
            ) == f'C:\\test{ext}'
            assert extract_uninstall_path(
                f'C:\\test{ext.upper()} /arg'
            ) == f'C:\\test{ext.upper()}'

    def test_aggressive_scenarios(self):
        """
        Test aggressive/offensive scenarios: unicode, long paths, multiple extensions
        """
        # Unicode/Multibyte characters
        assert extract_uninstall_path(
            'C:\\Program Files\\测试\\uninst.exe /S'
        ) == 'C:\\Program Files\\测试\\uninst.exe'

        # Path with dots that are not extensions
        assert extract_uninstall_path(
            'C:\\Program.Files\\App.v1\\uninst /S'
        ) == 'C:\\Program.Files\\App.v1\\uninst /S'

        # Extreme length
        long_path = 'C:\\' + 'a' * 100 + '\\uninst.exe /verb'
        assert extract_uninstall_path(long_path) == 'C:\\' + 'a' * 100 + '\\uninst.exe'

        # Multiple legitimate extensions, should pick the first one
        assert extract_uninstall_path('C:\\app.exe D:\\other.exe') == 'C:\\app.exe'

        # Extension buried in a part (must end with it)
        # 'C:\\myapp.exe.lnk /arg' -> parts are ['C:\\myapp.exe.lnk', '/arg']
        # 'C:\\myapp.exe.lnk' does not end with '.exe'
        assert extract_uninstall_path('C:\\myapp.exe.lnk /arg') == 'C:\\myapp.exe.lnk /arg'

        # Path ends with extension but has no space before it (one part)
        assert extract_uninstall_path('C:\\ProgramFiles\\App.exe') == 'C:\\ProgramFiles\\App.exe'

        # Mixed slashes
        assert extract_uninstall_path(
            'C:\\Program Files/My App\\uninstall.exe /S'
        ) == 'C:\\Program Files/My App\\uninstall.exe'

        # Extension is the only thing or part of a small thing
        assert extract_uninstall_path('a.exe /b') == 'a.exe'


class TestIterUninstallRegistry:
    """The whitelist of uninstall registry key names."""

    @staticmethod
    def _fake(tree, monkeypatch):
        fake = FakeWinreg(tree)
        monkeypatch.setattr(windows_reg, 'winreg', fake)
        return fake

    @staticmethod
    def _tree(entries):
        """Build a registry tree from {path: {key_name: uninstall_string}}."""
        tree = {}
        for path in (
            r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
            r'Software\Microsoft\Windows\CurrentVersion\Uninstall',
        ):
            values = entries.get(path, {})
            tree[path] = {'subkeys': list(values)}
            for name, uninstall in values.items():
                tree[f'{path}\\{name}'] = {'values': {'UninstallString': uninstall}}
        return tree

    def test_mumu_player_key_is_found(self, monkeypatch):
        """'MuMuPlayer' must stay in the whitelist.

        A missing comma used to merge it with 'MuMuPlayer-12.0' into a single
        meaningless string, so this key was silently skipped.
        """
        path = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
        self._fake(
            self._tree({path: {'MuMuPlayer': r'C:\Program Files\Nemu\uninstall.exe -u'}}),
            monkeypatch,
        )
        assert [str(x) for x in iter_uninstall_registry()] == [
            'C:/Program Files/Nemu/uninstall.exe'
        ]

    def test_mumu_player_12_key_is_found(self, monkeypatch):
        """'MuMuPlayer-12.0' is matched once, not duplicated by the fix."""
        path = r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
        self._fake(
            self._tree({path: {'MuMuPlayer-12.0': r'D:\MuMu12\uninstall.exe'}}),
            monkeypatch,
        )
        assert [str(x) for x in iter_uninstall_registry()] == ['D:/MuMu12/uninstall.exe']

    def test_unknown_keys_are_skipped(self, monkeypatch):
        """Keys outside the whitelist never yield anything."""
        path = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
        self._fake(
            self._tree({path: {
                'NotAnEmulator': r'C:\Other\uninstall.exe',
                'MuMuPlayer-12.0MuMuPlayer': r'C:\Junk\uninstall.exe',
            }}),
            monkeypatch,
        )
        assert list(iter_uninstall_registry()) == []

    def test_missing_key_is_skipped(self, monkeypatch):
        """A whitelisted name without an UninstallString is ignored."""
        path = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
        tree = self._tree({})
        tree[path] = {'subkeys': ['Nox']}
        self._fake(tree, monkeypatch)
        assert list(iter_uninstall_registry()) == []

    def test_empty_uninstall_string_is_skipped(self, monkeypatch):
        path = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
        self._fake(self._tree({path: {'Nox': ''}}), monkeypatch)
        assert list(iter_uninstall_registry()) == []

    def test_the_first_registry_hive_is_scanned_first(self, monkeypatch):
        path32 = r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
        path64 = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
        self._fake(
            self._tree({
                path32: {'Nox': r'C:\Nox32\uninstall.exe'},
                path64: {'Nox': r'C:\Nox64\uninstall.exe'},
            }),
            monkeypatch,
        )
        assert [str(x) for x in iter_uninstall_registry()] == [
            'C:/Nox32/uninstall.exe',
            'C:/Nox64/uninstall.exe',
        ]


class TestListRegAndListKey:
    def test_list_reg_reads_every_value(self, monkeypatch):
        monkeypatch.setattr(
            windows_reg, 'winreg', FakeWinreg({'K': {'values': {'a': '1', 'b': '2'}}})
        )
        with windows_reg.winreg.OpenKey(None, 'K') as reg:
            rows = list_reg(reg)
        assert [(r.name, r.value) for r in rows] == [('a', '1'), ('b', '2')]

    def test_list_reg_of_an_empty_key(self, monkeypatch):
        monkeypatch.setattr(windows_reg, 'winreg', FakeWinreg({'K': {}}))
        with windows_reg.winreg.OpenKey(None, 'K') as reg:
            assert list_reg(reg) == []

    def test_list_key_reads_every_subkey(self, monkeypatch):
        monkeypatch.setattr(
            windows_reg, 'winreg', FakeWinreg({'K': {'subkeys': ['one', 'two']}})
        )
        with windows_reg.winreg.OpenKey(None, 'K') as reg:
            assert list_key(reg) == ['one', 'two']

    def test_list_key_of_an_empty_key(self, monkeypatch):
        monkeypatch.setattr(windows_reg, 'winreg', FakeWinreg({'K': {}}))
        with windows_reg.winreg.OpenKey(None, 'K') as reg:
            assert list_key(reg) == []


class TestGetInstallDirFromReg:
    def test_current_user_wins(self, monkeypatch):
        monkeypatch.setattr(
            windows_reg, 'winreg', FakeWinreg({
                r'SOFTWARE\leidian\ldplayer': {'values': {'InstallDir': 'E:/HKCU'}},
            })
        )
        assert get_install_dir_from_reg(r'SOFTWARE\leidian\ldplayer', 'InstallDir') == 'E:/HKCU'

    def test_local_machine_is_the_fallback(self, monkeypatch):
        """Both hives are read through the same path, the first hit wins."""
        monkeypatch.setattr(
            windows_reg, 'winreg', FakeWinreg({
                r'SOFTWARE\leidian\ldplayer': {'values': {'InstallDir': 'E:/HKLM'}},
            })
        )
        assert get_install_dir_from_reg(r'SOFTWARE\leidian\ldplayer', 'InstallDir') == 'E:/HKLM'

    def test_missing_key(self, monkeypatch):
        monkeypatch.setattr(windows_reg, 'winreg', FakeWinreg({}))
        assert get_install_dir_from_reg(r'SOFTWARE\leidian\ldplayer', 'InstallDir') == ''

    def test_missing_value(self, monkeypatch):
        monkeypatch.setattr(
            windows_reg, 'winreg',
            FakeWinreg({r'SOFTWARE\leidian\ldplayer': {'values': {'Other': 'x'}}}),
        )
        assert get_install_dir_from_reg(r'SOFTWARE\leidian\ldplayer', 'InstallDir') == ''


class TestIterKnownRegistry:
    def test_ldplayer_install_dirs(self, monkeypatch):
        keys = {
            r'SOFTWARE\leidian\ldplayer': {'values': {'InstallDir': 'E:/LDPlayer'}},
            r'SOFTWARE\leidian\ldplayer9': {'values': {'InstallDir': 'E:/LDPlayer9'}},
            r'SOFTWARE\leidian\ldplayer14': {'values': {'InstallDir': 'E:/LDPlayer14'}},
        }
        monkeypatch.setattr(windows_reg, 'winreg', FakeWinreg(keys))
        assert [str(x) for x in iter_known_registry()] == [
            'E:/LDPlayer/dnplayer.exe',
            'E:/LDPlayer9/dnplayer.exe',
            'E:/LDPlayer14/dnplayer.exe',
        ]

    def test_nothing_installed(self, monkeypatch):
        monkeypatch.setattr(windows_reg, 'winreg', FakeWinreg({}))
        assert list(iter_known_registry()) == []


class TestIterMuiCache:
    PATH = r'Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache'

    def _fake(self, monkeypatch, values):
        monkeypatch.setattr(windows_reg, 'winreg', FakeWinreg({self.PATH: {'values': values}}))

    def test_executable_path_is_extracted(self, monkeypatch):
        """Entries carry a `.FriendlyAppName` suffix that has to be dropped."""
        self._fake(monkeypatch, {
            r'C:\Program Files\Nox\bin\Nox.exe.FriendlyAppName': 'Nox',
            r'E:\LDPlayer9\dnplayer.exe.FriendlyAppType': 'Application',
            'NotAPath': 'x',
        })
        assert [str(x) for x in iter_mui_cache()] == [
            'C:/Program Files/Nox/bin/Nox.exe',
            'E:/LDPlayer9/dnplayer.exe',
        ]

    def test_multi_instance_manager_is_expanded(self, monkeypatch):
        self._fake(monkeypatch, {
            r'E:\LDPlayer9\dnmultiplayer.exe.FriendlyAppName': 'dnmultiplayer',
        })
        assert [str(x) for x in iter_mui_cache()] == [
            'E:/LDPlayer9/dnmultiplayer.exe',
            'E:/LDPlayer9/dnplayer.exe',
        ]

    def test_missing_key(self, monkeypatch):
        monkeypatch.setattr(windows_reg, 'winreg', FakeWinreg({}))
        assert list(iter_mui_cache()) == []


class TestIterUserAssist:
    PATH = r'Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist'
    FOLDER = '{00000000-0000-0000-0000-000000000000}'

    def _fake(self, monkeypatch, programs):
        """`programs` are plain names, they are stored rot-13 encoded."""
        values = {codecs.encode(name, 'rot-13'): 1 for name in programs}
        monkeypatch.setattr(
            windows_reg, 'winreg', FakeWinreg({
                self.PATH: {'subkeys': [self.FOLDER]},
                f'{self.PATH}\\{self.FOLDER}\\Count': {'values': values},
            })
        )

    def test_rot13_names_are_decoded(self, monkeypatch):
        self._fake(monkeypatch, [r'C:\Program Files\Nox\bin\Nox.exe'])
        assert [str(x) for x in iter_user_assist()] == ['C:/Program Files/Nox/bin/Nox.exe']

    def test_entries_with_a_guid_are_skipped(self, monkeypatch):
        """UserAssist also records GUID-keyed control panel items."""
        self._fake(monkeypatch, [
            r'{00000000-1111-2222-3333-000000000000}\some.exe',
            r'E:\LDPlayer9\dnplayer.exe',
        ])
        assert [str(x) for x in iter_user_assist()] == ['E:/LDPlayer9/dnplayer.exe']

    def test_multi_instance_manager_is_expanded(self, monkeypatch):
        self._fake(monkeypatch, [r'E:\LDPlayer9\dnmultiplayer.exe'])
        assert [str(x) for x in iter_user_assist()] == [
            'E:/LDPlayer9/dnmultiplayer.exe',
            'E:/LDPlayer9/dnplayer.exe',
        ]

    def test_missing_key(self, monkeypatch):
        monkeypatch.setattr(windows_reg, 'winreg', FakeWinreg({}))
        assert list(iter_user_assist()) == []

    def test_folder_without_a_count_key_is_skipped(self, monkeypatch):
        monkeypatch.setattr(
            windows_reg, 'winreg',
            FakeWinreg({self.PATH: {'subkeys': [self.FOLDER]}}),
        )
        assert list(iter_user_assist()) == []
