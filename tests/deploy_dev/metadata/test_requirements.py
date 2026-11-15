"""
Tests for the header block of a METADATA and the dependency names it declares
(alasio/deploy_dev/metadata/requirements.py).

The tests cover the header block (a repeated header is a dependency), the
reading of a wheel held in memory, and the marker evaluation that decides which
dependency belongs to a client. The reading of a version from its index is
tested in tests/deploy_dev/metadata/test_provider.py, where the provider lives.
"""
import io
import zipfile

import pytest

from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy_dev.metadata.requirements import (
    PLATFORMS, marker_environments, requirement_names, requires_dist, wheel_metadata
)
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pypi_mock import make_wheel, metadata_text

pytestmark = [
    pytest.mark.parallel,
]

# The target of the tests: a fixed python 3.8, whatever runs the suite
TARGET = TargetPython(py_version_info=(3, 8, 20))


def archive(members):
    """
    Build a zip archive in memory.

    Args:
        members (dict[str, bytes | str]): {path in the archive: content}

    Returns:
        bytes: The archive
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        for path, content in members.items():
            zf.writestr(path, content)
    return buffer.getvalue()


class TestRequiresDist:
    """The dependency lines of a METADATA file."""

    def test_repeated_lines_are_kept(self):
        """Requires-Dist repeats once per dependency, a dict would lose all but one."""
        content = metadata_text('demo', '1.0', ['a>=1', 'b'])

        assert requires_dist(content) == ['a>=1', 'b']

    def test_the_body_is_not_read(self):
        """The body holds the long description, the headers end at the empty line."""
        content = 'Name: demo\n\nRequires-Dist: not-a-header\n'

        assert requires_dist(content) == []

    def test_a_width_only_continuation_is_not_the_body(self):
        """A folded value holds lines of the width only: the block goes on.

        The License of a METADATA built by a backend is such a value, and the
        headers the closure needs, e.g. Requires-Dist, come after it.
        """
        content = (
            'Name: h2\n'
            'License: The MIT License (MIT)\n'
            '        \n'
            '        Copyright (c) 2015-2020 Cory Benfield\n'
            '        \n'
            'Requires-Dist: hyperframe<7,>=6.1\n'
            'Requires-Dist: hpack<5,>=4.2\n'
            '\n'
            'h2: HTTP/2 Protocol Stack\n'
        )

        assert requires_dist(content) == ['hyperframe<7,>=6.1', 'hpack<5,>=4.2']

    def test_a_marker_is_kept_as_it_is_written(self):
        """The reader splits no marker: it is evaluated later, per platform."""
        content = metadata_text('demo', '1.0', ['a>=1', 'b; extra == "x"'])

        assert requires_dist(content) == ['a>=1', 'b; extra == "x"']

    def test_no_dependency(self):
        assert requires_dist(metadata_text('demo', '1.0')) == []


class TestWheelMetadata:
    """The METADATA of a wheel, the fallback of an index without PEP 658."""

    def test_read(self):
        metadata = wheel_metadata(make_wheel('demo', '1.0', ['a']))

        assert requires_dist(metadata) == ['a']

    def test_no_metadata(self):
        with pytest.raises(ValueError, match='missing .dist-info/METADATA'):
            wheel_metadata(archive({'demo/__init__.py': ''}))

    def test_not_a_wheel(self):
        with pytest.raises(zipfile.BadZipFile):
            wheel_metadata(b'not an archive')


class TestMarkerEnvironments:
    """The platforms the closure is the union of."""

    def test_every_client_platform(self):
        environments = list(marker_environments(TARGET))

        assert [(env['sys_platform'], env['platform_system'], env['platform_machine'])
                for env in environments] == list(PLATFORMS)
        assert environments[0]['python_version'] == '3.8'
        assert environments[0]['python_full_version'] == '3.8.20'
        assert environments[0]['os_name'] == 'nt'
        assert environments[1]['os_name'] == 'posix'


class TestRequirementNames:
    """The names a Requires-Dist list asks for."""

    def test_a_plain_dependency(self):
        assert requirement_names(['a>=1', 'b'], [''], TARGET) == {'a': frozenset(), 'b': frozenset()}

    def test_the_order_is_the_metadata_order(self):
        assert list(requirement_names(['b', 'a'], [''], TARGET)) == ['b', 'a']

    def test_a_repeated_dependency_is_one_name(self):
        assert list(requirement_names(['a>=1', 'a[x]<2'], [''], TARGET)) == ['a']

    def test_the_extras_of_the_requirement_are_collected(self):
        assert requirement_names(['httpx[http2,cli]>=0.1'], [''], TARGET) == {
            'httpx': frozenset({'http2', 'cli'})}

    def test_the_marker_of_an_extra(self):
        """A dependency of an extra is in the closure only when the extra is."""
        requires = ['a; extra == "http2"']

        assert requirement_names(requires, [''], TARGET) == {}
        assert requirement_names(requires, ['', 'http2'], TARGET) == {'a': frozenset()}

    def test_the_union_of_the_platforms(self):
        """A dependency of any client platform is in the closure."""
        requires = ['a; sys_platform == "linux"', 'b; sys_platform == "freebsd"']

        assert requirement_names(requires, [''], TARGET) == {'a': frozenset()}

    def test_the_python_version_of_the_target(self):
        requires = ['a; python_version >= "3.9"', 'b; python_version < "3.9"']

        assert requirement_names(requires, [''], TARGET) == {'b': frozenset()}

    def test_an_invalid_requirement_is_reported(self):
        with logger.mock_capture_writer() as capture:
            names = requirement_names(['not a requirement !'], [''], TARGET)

        assert names == {}
        assert capture.fd.any_contains('Skipped a requirement of a distribution')
