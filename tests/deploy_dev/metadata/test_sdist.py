"""
Tests for the metadata of a source distribution, read without building it
(alasio/deploy_dev/metadata/sdist.py).
"""
import io
import tarfile
import zipfile

import pytest

from alasio.deploy_dev.metadata.sdist import MAX_METADATA_SIZE, is_sdist, read_sdist_metadata, sdist_name_of
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pypi_mock import make_sdist

pytestmark = [
    pytest.mark.parallel,
]


def make_zip_sdist(name, version, members):
    """
    A source distribution as a zip archive.

    Args:
        name (str): Distribution name
        version (str): Version of the distribution
        members (dict[str, str]): {path in the archive: content}

    Returns:
        bytes: Content of the .zip file
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        for path, content in members.items():
            zf.writestr(f'{name}-{version}/{path}', content)
    return buffer.getvalue()


def pkg_info(requires=(), metadata_version='2.1', name='demo', version='1.0'):
    """
    The PKG-INFO of a distribution.

    Args:
        requires (Iterable[str]): The Requires-Dist values
        metadata_version (str): Core metadata version
        name (str): Distribution name
        version (str): Version

    Returns:
        str: Content of the file
    """
    lines = [
        f'Metadata-Version: {metadata_version}',
        f'Name: {name}',
        f'Version: {version}',
        *[f'Requires-Dist: {text}' for text in requires],
    ]
    return '\n'.join(lines) + '\n'


class TestIsSdist:
    """The filenames a source distribution is published as."""

    @pytest.mark.parametrize('filename, expected', [
        ('demo-1.0.tar.gz', True),
        ('demo-1.0.zip', True),
        ('demo-1.0.tar.bz2', True),
        ('demo-1.0.tar.xz', True),
        ('demo-1.0.tgz', True),
        ('demo-1.0-py3-none-any.whl', False),
        ('demo-1.0', False),
    ])
    def test_suffixes(self, filename, expected):
        assert is_sdist(filename) is expected

    @pytest.mark.parametrize('filename, expected', [
        ('demo-1.0.tar.gz', ('demo', '1.0')),
        ('demo-package-2.0rc1.tar.gz', ('demo-package', '2.0rc1')),
        ('demo-1.0-py3-none-any.whl', None),
        ('demo.tar.gz', None),
    ])
    def test_the_name_and_the_version(self, filename, expected):
        assert sdist_name_of(filename) == expected


class TestReadSdistMetadata:
    """The dependencies an sdist declares, read without building it."""

    def test_the_requires_dist_of_the_pkg_info(self):
        content = make_sdist('demo', '1.0', ['leaf>=1', 'other; python_version < "3.9"'])

        assert read_sdist_metadata(content, 'demo-1.0.tar.gz') == [
            'leaf>=1', 'other; python_version < "3.9"']

    def test_the_egg_info_record_of_a_legacy_sdist(self):
        """Core metadata 1.1 has no Requires-Dist: the setuptools record has them."""
        content = make_sdist(
            'demo', '1.0', metadata_version='1.1',
            requires_txt='pyelftools\ncigam\nxmltodict\n')
        # the PKG-INFO of the fixture carries no requires, the record is used
        assert read_sdist_metadata(content, 'demo-1.0.tar.gz') == [
            'pyelftools', 'cigam', 'xmltodict']

    def test_the_extras_of_the_egg_info_record(self):
        content = make_sdist(
            'demo', '1.0', metadata_version='1.1',
            requires_txt='leaf\n\n[test]\npytest\n\n[http2]\nh2\n')

        assert read_sdist_metadata(content, 'demo-1.0.tar.gz') == [
            'leaf',
            'pytest; extra == "test"',
            'h2; extra == "http2"',
        ]

    def test_a_requirement_of_the_record_with_a_marker(self):
        content = make_sdist(
            'demo', '1.0', metadata_version='1.1',
            requires_txt='colorama; sys_platform == "win32"')

        assert read_sdist_metadata(content, 'demo-1.0.tar.gz') == [
            'colorama; sys_platform == "win32"']

    def test_no_static_metadata(self):
        content = make_sdist('demo', '1.0', metadata_version='1.1')

        with pytest.raises(ValueError, match='declares no static dependency metadata'):
            read_sdist_metadata(content, 'demo-1.0.tar.gz')

    def test_a_zip_sdist(self):
        content = make_zip_sdist('demo', '1.0', {'PKG-INFO': pkg_info(['leaf'])})

        assert read_sdist_metadata(content, 'demo-1.0.zip') == ['leaf']

    def test_a_file_that_is_not_an_archive(self):
        with pytest.raises(ValueError, match='cannot be read'):
            read_sdist_metadata(b'not an archive', 'demo-1.0.tar.gz')

    def test_an_include_of_the_record(self):
        content = make_sdist(
            'demo', '1.0', metadata_version='1.1',
            requires_txt='-r base.txt\nleaf\n')

        with pytest.raises(ValueError, match='includes another file'):
            read_sdist_metadata(content, 'demo-1.0.tar.gz')

    def test_a_line_that_is_not_a_requirement(self):
        content = make_sdist(
            'demo', '1.0', metadata_version='1.1',
            requires_txt='not a requirement !\n')

        with pytest.raises(ValueError, match='not a requirement'):
            read_sdist_metadata(content, 'demo-1.0.tar.gz')

    def test_a_conditional_section_of_the_record(self):
        content = make_sdist(
            'demo', '1.0', metadata_version='1.1',
            requires_txt='[:python_version < "3.9"]\nleaf\n')

        with pytest.raises(ValueError, match='conditional section'):
            read_sdist_metadata(content, 'demo-1.0.tar.gz')

    def test_a_metadata_file_of_an_impossible_size(self):
        """An archive must not be able to make the reader allocate its content."""
        content = make_sdist('demo', '1.0', metadata_version='1.1')
        with tarfile.open(fileobj=io.BytesIO(content), mode='r:*') as tf:
            members = [member for member in tf.getmembers() if member.name.endswith('PKG-INFO')]
        assert members and members[0].size < MAX_METADATA_SIZE
