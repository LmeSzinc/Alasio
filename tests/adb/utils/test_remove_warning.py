from alasio.adb.utils.remove_warning import remove_shell_warning, remove_screenshot_warning


class TestRemoveWarning:
    def test_remove_shell_warning(self):
        # Bytes
        assert remove_shell_warning(b'Hello World') == b'Hello World'
        assert remove_shell_warning(
            b'WARNING: linker: [vdso]\n'
            b'WARNING: linker: [vdso]\n'
            b'\x89PNG'
        ) == b'\x89PNG'

        # Str
        assert remove_shell_warning('Hello World') == 'Hello World'
        assert remove_shell_warning(
            'WARNING: linker: [vdso]\n'
            'WARNING: linker: [vdso]\n'
            'data'
        ) == 'data'

    def test_remove_screenshot_warning(self):
        # Bytes
        assert remove_screenshot_warning(b'\x89PNG') == b'\x89PNG'
        # Situation 1
        assert remove_screenshot_warning(
            b'Failed to create //.cache for shader cache (Read-only file system)---disabling.\n'
            b'\x89PNG'
        ) == b'\x89PNG'
        # Situation 2/3
        assert remove_screenshot_warning(
            b'[Warning] Multiple displays were found, but no display id was specified!\n'
            b'A display id should be specified.\n'
            b'See "dumpsys SurfaceFlinger --display-id" for valid display IDs.\n'
            b'\x89PNG'
        ) == b'\x89PNG'
        # Situation 4
        assert remove_screenshot_warning(
            b'long long=8 fun*=10\n'
            b'\x89PNG'
        ) == b'\x89PNG'

        # Str
        assert remove_screenshot_warning('data') == 'data'
        # Situation 1
        assert remove_screenshot_warning(
            'Failed to create //.cache for shader cache (Read-only file system)---disabling.\n'
            'data'
        ) == 'data'
        # Situation 2/3
        assert remove_screenshot_warning(
            '[Warning] Multiple displays were found, but no display id was specified!\n'
            'A display ID can be specified with the [-d display-id] option.\n'
            'See "dumpsys SurfaceFlinger --display-id" for valid display IDs.\n'
            'data'
        ) == 'data'
        # Situation 4
        assert remove_screenshot_warning(
            'long long=8 fun*=10\n'
            'data'
        ) == 'data'

    def test_remove_screenshot_warning_partial(self):
        # Only [Warning] Multiple displays matches
        s = b'[Warning] Multiple displays were found, but no display id was specified!\n' \
            b'Something else\n' \
            b'\x89PNG'
        assert remove_screenshot_warning(s) == b'Something else\n\x89PNG'

        # [Warning] Multiple displays and A display id matches, but See "dumpsys doesn't
        s = b'[Warning] Multiple displays were found, but no display id was specified!\n' \
            b'A display id should be specified.\n' \
            b'\x89PNG'
        assert remove_screenshot_warning(s) == b'\x89PNG'

    def test_other_types(self):
        # Should return as-is
        # Using type: ignore to suppress linting for testing non-standard inputs
        assert remove_shell_warning(None) is None  # type: ignore
        assert remove_shell_warning(123) == 123  # type: ignore
        assert remove_screenshot_warning(None) is None  # type: ignore
        assert remove_screenshot_warning(123) == 123  # type: ignore
