"""
Tests for the wheel model (pip/_internal/models/wheel.py) and the filename
parse it uses (packaging/utils.py).
"""
import pytest

from alasio.deploy.pip_download._tags import Tag
from alasio.deploy.pip_download._utils import InvalidWheelFilename, parse_wheel_filename
from alasio.deploy.pip_download._wheel import Wheel


class TestParseWheelFilename:
    """The filename is parsed into its name, version, build tag and tags."""

    @pytest.mark.parametrize('filename, name, version, build, tags', [
        ('httpx-0.28.1-py3-none-any.whl', 'httpx', '0.28.1', (),
         {'py3-none-any'}),
        ('numpy-2.1.0-cp312-cp312-win_amd64.whl', 'numpy', '2.1.0', (),
         {'cp312-cp312-win_amd64'}),
        ('foo-1.0-1-py3-none-any.whl', 'foo', '1.0', (1, ''),
         {'py3-none-any'}),
        ('foo-1.0-1abc-py3-none-any.whl', 'foo', '1.0', (1, 'abc'),
         {'py3-none-any'}),
        ('foo-1.0-py2.py3-none-any.whl', 'foo', '1.0', (),
         {'py2-none-any', 'py3-none-any'}),
        ('my_pkg-1.0.0-py3-none-any.whl', 'my-pkg', '1.0.0', (),
         {'py3-none-any'}),
        ('zope.interface-6.0-cp38-cp38-manylinux_2_17_x86_64.manylinux2014_x86_64.whl',
         'zope-interface', '6.0', (),
         {'cp38-cp38-manylinux_2_17_x86_64', 'cp38-cp38-manylinux2014_x86_64'}),
    ])
    def test_parse(self, filename, name, version, build, tags):
        parsed_name, parsed_version, parsed_build, parsed_tags = parse_wheel_filename(filename)
        assert parsed_name == name
        assert str(parsed_version) == version
        assert parsed_build == build
        assert {str(tag) for tag in parsed_tags} == tags

    @pytest.mark.parametrize('filename', [
        'foo-1.0.tar.gz',
        'foo-1.0-py3-none-any.whl.metadata',
        'foo-1.0-py3-none.whl',
        'bad-name-1.0-1-2-py3-none-any.whl',
        'foo-1.0-py3-none-any',
        '__foo-1.0-py3-none-any.whl',
    ])
    def test_invalid(self, filename):
        with pytest.raises(InvalidWheelFilename):
            parse_wheel_filename(filename)


class TestWheel:
    """The wheel model, the tag queries of the fetcher."""

    def test_parts(self):
        wheel = Wheel('numpy-2.1.0-cp312-cp312-win_amd64.whl')
        assert wheel.filename == 'numpy-2.1.0-cp312-cp312-win_amd64.whl'
        assert wheel.name == 'numpy'
        assert wheel.version == '2.1.0'
        assert wheel.build_tag == ()
        assert wheel.get_formatted_file_tags() == ['cp312-cp312-win_amd64']

    def test_invalid(self):
        with pytest.raises(InvalidWheelFilename):
            Wheel('foo-1.0.tar.gz')

    def test_supported(self):
        wheel = Wheel('foo-1.0-cp38-cp38-win_amd64.whl')
        assert wheel.supported([Tag('cp38', 'cp38', 'win_amd64')])
        assert not wheel.supported([Tag('cp38', 'cp38', 'linux_x86_64')])
        assert not wheel.supported([])

    def test_support_index_min(self):
        wheel = Wheel('foo-1.0-py3-none-any.whl')
        tags = [
            Tag('cp38', 'cp38', 'win_amd64'),
            Tag('py3', 'none', 'any'),
            Tag('py38', 'none', 'any'),
        ]
        assert wheel.support_index_min(tags) == 1
        with pytest.raises(ValueError):
            wheel.support_index_min(tags[:1])

    def test_find_most_preferred_tag(self):
        wheel = Wheel('foo-1.0-py3-none-any.whl')
        priority = {
            Tag('cp38', 'cp38', 'win_amd64'): 0,
            Tag('py3', 'none', 'any'): 7,
        }
        assert wheel.find_most_preferred_tag(list(priority), priority) == 7
        with pytest.raises(ValueError):
            wheel.find_most_preferred_tag(
                [Tag('cp38', 'cp38', 'win_amd64')],
                {Tag('cp38', 'cp38', 'win_amd64'): 0})
