from alasio.device.search.windows_reg import extract_uninstall_path


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
