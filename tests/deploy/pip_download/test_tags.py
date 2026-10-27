"""
Tests for the wheel tag port (packaging/tags.py, _manylinux.py, _musllinux.py).

The expected sequences are the ones packaging generates for the same inputs:
the sequences are compared whole, so the order (the preference of PEP 425) is
part of the test.
"""
import sys

import pytest

from alasio.deploy.pip_download import _manylinux, _musllinux, _tags
from alasio.deploy.pip_download._tags import (
    Tag, compatible_tags, cpython_tags, interpreter_name, interpreter_version, mac_platforms, parse_tag, sys_tags
)


class TestTag:
    """The tag triple."""

    def test_str(self):
        assert str(Tag('py3', 'none', 'any')) == 'py3-none-any'

    def test_lower(self):
        assert str(Tag('PY3', 'NONE', 'ANY')) == 'py3-none-any'

    def test_equal(self):
        assert Tag('py3', 'none', 'any') == Tag('PY3', 'None', 'Any')
        assert Tag('py3', 'none', 'any') != Tag('py2', 'none', 'any')
        assert hash(Tag('py3', 'none', 'any')) == hash(Tag('py3', 'none', 'any'))

    def test_compare_with_other_type(self):
        assert Tag('py3', 'none', 'any') != 'py3-none-any'


class TestParseTag:
    """A tag string names one or several tags."""

    def test_simple(self):
        assert parse_tag('py3-none-any') == frozenset({Tag('py3', 'none', 'any')})

    def test_compressed(self):
        assert parse_tag('py2.py3-none-any') == frozenset({
            Tag('py2', 'none', 'any'),
            Tag('py3', 'none', 'any'),
        })

    def test_compressed_platform(self):
        assert len(parse_tag('cp38-cp38-manylinux_2_17_x86_64.manylinux2014_x86_64')) == 2

    @pytest.mark.parametrize('text', ['py3-none', 'py3-none-any-extra', 'py3--any', ''])
    def test_invalid(self, text):
        with pytest.raises(_tags.InvalidTag):
            parse_tag(text)

    def test_validate_order(self):
        assert parse_tag('py2.py3-none-any', validate_order=True) == parse_tag('py2.py3-none-any')
        with pytest.raises(_tags.InvalidTag):
            parse_tag('py3.py2-none-any', validate_order=True)


class TestCpythonTags:
    """The tags of a CPython interpreter."""

    def test_fixed_python38(self):
        assert [str(tag) for tag in cpython_tags((3, 8), ['cp38'], ['win_amd64'])] == [
            'cp38-cp38-win_amd64',
            'cp38-abi3-win_amd64',
            'cp38-none-win_amd64',
            'cp37-abi3-win_amd64',
            'cp36-abi3-win_amd64',
            'cp35-abi3-win_amd64',
            'cp34-abi3-win_amd64',
            'cp33-abi3-win_amd64',
            'cp32-abi3-win_amd64',
        ]

    def test_abi3_removed_from_the_abis(self):
        # an explicit abi3 in the ABI list is not repeated before the abi3
        # block, 'none' neither
        assert [str(tag) for tag in cpython_tags((3, 3), ['cp33', 'abi3', 'none'], ['any'])] == [
            'cp33-cp33-any',
            'cp33-abi3-any',
            'cp33-none-any',
            'cp32-abi3-any',
        ]


class TestCompatibleTags:
    """The tags of a wheel of any interpreter."""

    def test_fixed_python38(self):
        assert [str(tag) for tag in compatible_tags((3, 8), 'cp38', ['win_amd64'])] == [
            'py38-none-win_amd64',
            'py3-none-win_amd64',
            'py37-none-win_amd64',
            'py36-none-win_amd64',
            'py35-none-win_amd64',
            'py34-none-win_amd64',
            'py33-none-win_amd64',
            'py32-none-win_amd64',
            'py31-none-win_amd64',
            'py30-none-win_amd64',
            'cp38-none-any',
            'py38-none-any',
            'py3-none-any',
            'py37-none-any',
            'py36-none-any',
            'py35-none-any',
            'py34-none-any',
            'py33-none-any',
            'py32-none-any',
            'py31-none-any',
            'py30-none-any',
        ]

    def test_no_interpreter(self):
        assert 'cp38-none-any' not in [
            str(tag) for tag in compatible_tags((3, 8), None, ['any'])]


class TestMacPlatforms:
    """The macOS platform families."""

    def test_macos11_arm64(self):
        assert list(mac_platforms((11, 0), 'arm64')) == [
            'macosx_11_0_arm64',
            'macosx_11_0_universal2',
        ] + [f'macosx_10_{minor}_universal2' for minor in range(16, 3, -1)]

    def test_macos10_9_x86_64(self):
        assert list(mac_platforms((10, 9), 'x86_64'))[:6] == [
            'macosx_10_9_x86_64',
            'macosx_10_9_intel',
            'macosx_10_9_fat64',
            'macosx_10_9_fat32',
            'macosx_10_9_universal2',
            'macosx_10_9_universal',
        ]

    def test_old_version(self):
        # 10.4 is the first version an x86_64 binary supports
        assert list(mac_platforms((10, 3), 'x86_64')) == []


class TestLinuxPlatforms:
    """The linux platform families, with the libc versions patched."""

    @pytest.fixture
    def linux(self, monkeypatch):
        monkeypatch.setattr(_tags.sysconfig, 'get_platform', lambda: 'linux-x86_64')
        monkeypatch.setattr(_manylinux, '_get_glibc_version', lambda: (2, 17))
        monkeypatch.setattr(_musllinux, '_get_musl_version', lambda executable: None)
        return _tags

    def test_manylinux_family(self, linux):
        platforms = list(linux._linux_platforms())
        assert platforms[:4] == [
            'manylinux_2_17_x86_64',
            'manylinux2014_x86_64',
            'manylinux_2_16_x86_64',
            'manylinux_2_15_x86_64',
        ]
        assert platforms[-4:] == [
            'manylinux_2_6_x86_64',
            'manylinux_2_5_x86_64',
            'manylinux1_x86_64',
            'linux_x86_64',
        ]
        assert 'manylinux2010_x86_64' in platforms

    def test_musllinux_family(self, linux, monkeypatch):
        monkeypatch.setattr(_musllinux, '_get_musl_version', lambda executable: _musllinux._MuslVersion(1, 2))
        monkeypatch.setattr(_manylinux, '_get_glibc_version', lambda: (-1, -1))
        assert list(linux._linux_platforms()) == [
            'musllinux_1_2_x86_64',
            'musllinux_1_1_x86_64',
            'musllinux_1_0_x86_64',
            'linux_x86_64',
        ]

    def test_no_libc(self, linux, monkeypatch):
        # a libc that cannot be detected produces no manylinux tag, the same
        # answer as packaging
        monkeypatch.setattr(_manylinux, '_get_glibc_version', lambda: (-1, -1))
        assert list(linux._linux_platforms()) == ['linux_x86_64']


class TestSysTags:
    """The tags of the running interpreter."""

    def test_first_tag(self):
        tags = [str(tag) for tag in sys_tags()]
        name = interpreter_name()
        if name == 'cp':
            version = interpreter_version()
            assert tags[0].startswith(f'cp{version}-cp{version}-')
        assert len(tags) == len(set(tags))

    def test_pure_python_last(self):
        tags = [str(tag) for tag in sys_tags()]
        assert tags[-1] == 'py30-none-any'
        assert 'py3-none-any' in tags

    def test_matches_the_running_python(self):
        tags = list(sys_tags())
        assert Tag(f'cp{sys.version_info[0]}{sys.version_info[1]}',
                   f'cp{sys.version_info[0]}{sys.version_info[1]}',
                   tags[0].platform) in tags
