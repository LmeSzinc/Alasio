"""
Tests for the pack channel decision (alasio/deploy_dev/pack_deps/resolve.py).

The decision is a property of the wheels one pinned version publishes: the
tests cover the tag rules against a fixed python 3.8 target (the decision must
not depend on the interpreter that runs the suite, the tool is run inside the
runtime environment by the mod author), the wheel selection of a page, and the
four answers a version can get.
"""
import pytest

from alasio.deploy.pip_download._link import Link
from alasio.deploy.pip_download._tags import Tag
from alasio.deploy.pip_download._target_python import TargetPython
from alasio.deploy.pip_download._wheel import Wheel
from alasio.deploy.pip_download.pypi_file import WheelCandidate, WheelNotFoundError
from alasio.deploy_dev.pack_deps.resolve import (
    DECISION_PACK, DECISION_PIP, REASON_COMPILED, REASON_NO_PURE, REASON_NOT_INSTALLABLE, REASON_PURE_ONLY, decide,
    decision_text, tag_covers_target, wheel_compiled_for_target, wheel_pure_for_target, wheels_of
)
from alasio.logger import logger

pytestmark = [
    pytest.mark.parallel,
]

# The target of the tests: a fixed python 3.8, whatever runs the suite
TARGET = TargetPython(py_version_info=(3, 8, 20))


def candidate(filename, requires_python=None, yanked=None):
    """
    One wheel of an index page.

    Args:
        filename (str): Filename of the wheel
        requires_python (str): Value of `data-requires-python`
        yanked (str): Value of `data-yanked`

    Returns:
        WheelCandidate: The link and the parsed filename
    """
    link = Link(
        f'http://index.example/simple/demo/{filename}',
        requires_python=requires_python,
        yanked_reason=yanked,
    )
    return WheelCandidate(link, Wheel(filename))


class TestTagCoversTarget:
    """Which wheel tags a python 3.8 CPython can install."""

    @pytest.mark.parametrize('interpreter, abi, platform_name, expected', [
        # a pure wheel of every python 3
        ('py3', 'none', 'any', True),
        ('py3', 'none', 'win_amd64', True),
        # a pure wheel of one version
        ('py38', 'none', 'any', True),
        ('py39', 'none', 'any', False),
        ('py313', 'none', 'any', False),
        # python 2 is not python 3
        ('py2', 'none', 'any', False),
        # a build of the target version
        ('cp38', 'cp38', 'win_amd64', True),
        ('cp38', 'cp38', 'manylinux_2_17_x86_64', True),
        ('cp37', 'cp37', 'win_amd64', False),
        ('cp39', 'cp39', 'win_amd64', False),
        # the stable abi: the version it names, and every newer one
        ('cp35', 'abi3', 'win_amd64', True),
        ('cp38', 'abi3', 'win_amd64', True),
        ('cp39', 'abi3', 'win_amd64', False),
        # the old abi of the builds before python 3.8
        ('cp38', 'cp38m', 'win_amd64', False),
        # another implementation
        ('pp38', 'pp38', 'win_amd64', False),
    ])
    def test_tag(self, interpreter, abi, platform_name, expected):
        tag = Tag(interpreter, abi, platform_name)

        assert tag_covers_target(tag, TARGET) is expected


class TestWheelForTarget:
    """The two kinds of wheel the decision looks for."""

    def test_pure(self):
        wheel = Wheel('demo-1.0-py3-none-any.whl')

        assert wheel_pure_for_target(wheel, TARGET) is True
        assert wheel_compiled_for_target(wheel, TARGET) is False

    def test_compiled(self):
        wheel = Wheel('demo-1.0-cp38-cp38-win_amd64.whl')

        assert wheel_pure_for_target(wheel, TARGET) is False
        assert wheel_compiled_for_target(wheel, TARGET) is True

    def test_compiled_for_another_python(self):
        """A build of a newer CPython is not a build the target can install."""
        wheel = Wheel('demo-1.0-cp39-cp39-win_amd64.whl')

        assert wheel_compiled_for_target(wheel, TARGET) is False

    def test_a_pure_wheel_of_a_newer_python_is_not_pure(self):
        """py313-none-any is pure, and useless to a python 3.8 client."""
        wheel = Wheel('demo-1.0-py313-none-any.whl')

        assert wheel_pure_for_target(wheel, TARGET) is False

    def test_the_abi3_build(self):
        assert wheel_compiled_for_target(Wheel('demo-1.0-cp35-abi3-win_amd64.whl'), TARGET) is True
        assert wheel_compiled_for_target(Wheel('demo-1.0-cp39-abi3-win_amd64.whl'), TARGET) is False


class TestDecide:
    """The four answers of a pinned version."""

    def test_pure_only_is_pack(self):
        decision = decide(
            'demo', '1.0', 'http://index.example/simple/demo/',
            [candidate('demo-1.0-py3-none-any.whl')], TARGET)

        assert decision.decision == DECISION_PACK
        assert decision.reason == REASON_PURE_ONLY
        assert decision.pure_wheel == 'demo-1.0-py3-none-any.whl'
        assert decision.compiled_wheels == []
        assert decision.pack is True
        assert decision.source == 'http://index.example/simple/demo/'

    def test_a_compiled_build_of_the_target_is_pip(self):
        """The pack would replace the build of the client with the pure one."""
        decision = decide(
            'demo', '1.0', '',
            [candidate('demo-1.0-py3-none-any.whl'),
             candidate('demo-1.0-cp38-cp38-win_amd64.whl')], TARGET)

        assert decision.decision == DECISION_PIP
        assert decision.reason == REASON_COMPILED
        assert decision.compiled_wheels == ['demo-1.0-cp38-cp38-win_amd64.whl']
        assert decision.pack is False

    def test_a_compiled_build_of_another_python_is_pack(self):
        decision = decide(
            'demo', '1.0', '',
            [candidate('demo-1.0-py3-none-any.whl'),
             candidate('demo-1.0-cp39-cp39-win_amd64.whl')], TARGET)

        assert decision.decision == DECISION_PACK
        assert decision.reason == REASON_PURE_ONLY

    def test_a_build_of_every_platform_is_pip(self):
        """The platform is ignored: a build of one client keeps the pin on pip."""
        decision = decide(
            'demo', '1.0', '',
            [candidate('demo-1.0-py3-none-any.whl'),
             candidate('demo-1.0-cp38-cp38-manylinux_2_17_x86_64.whl')], TARGET)

        assert decision.decision == DECISION_PIP
        assert decision.reason == REASON_COMPILED

    def test_only_compiled_wheels_is_pip(self):
        """The pack channel cannot build a pack without a pure wheel."""
        decision = decide(
            'demo', '1.0', '',
            [candidate('demo-1.0-cp38-cp38-win_amd64.whl')], TARGET)

        assert decision.decision == DECISION_PIP
        assert decision.reason == REASON_NO_PURE
        assert decision.pure_wheel == ''
        assert decision.compiled_wheels == ['demo-1.0-cp38-cp38-win_amd64.whl']

    def test_no_wheel_installs_on_the_target(self):
        """Only wheels of another python: the client cannot install the pin."""
        with logger.mock_capture_writer() as capture:
            decision = decide(
                'demo', '1.0', '',
                [candidate('demo-1.0-cp39-cp39-win_amd64.whl', requires_python='>=3.9')], TARGET)

        assert decision.decision == DECISION_PIP
        assert decision.reason == REASON_NOT_INSTALLABLE
        assert decision.compiled_wheels == []
        assert capture.fd.any_contains('installs on python 3.8')
        assert capture.fd.any_contains('Requires-Python >=3.9')

    def test_requires_python_of_a_compiled_wheel(self):
        """A build that declares a newer python is not a build of the target."""
        decision = decide(
            'demo', '1.0', '',
            [candidate('demo-1.0-py3-none-any.whl'),
             candidate('demo-1.0-cp38-cp38-win_amd64.whl', requires_python='>=3.9')], TARGET)

        assert decision.decision == DECISION_PACK
        assert decision.reason == REASON_PURE_ONLY

    def test_an_invalid_requires_python_is_ignored(self):
        """A broken specifier of an index must not hide the file, like pip."""
        decision = decide(
            'demo', '1.0', '',
            [candidate('demo-1.0-py3-none-any.whl', requires_python='not a specifier')], TARGET)

        assert decision.decision == DECISION_PACK


class TestWheelsOf:
    """The wheels of one version, from the links of a page."""

    def test_yanked_is_dropped(self, index):
        index.add('demo', '1.0', tag='cp38-cp38-win_amd64', yanked='broken build')
        index.add('demo', '1.0')

        names = [item.filename for item in wheels_of(index.links('demo'), 'demo', '1.0')]

        assert names == ['demo-1.0-py3-none-any.whl']

    def test_every_yanked_is_kept(self, index):
        """PEP 592: a pinned version with nothing else still installs."""
        index.add('demo', '1.0', yanked='broken build')

        names = [item.filename for item in wheels_of(index.links('demo'), 'demo', '1.0')]

        assert names == ['demo-1.0-py3-none-any.whl']

    def test_another_version_is_not_selected(self, index):
        index.add('demo', '1.0')
        index.add('demo', '2.0')

        names = [item.filename for item in wheels_of(index.links('demo'), 'demo', '2.0')]

        assert names == ['demo-2.0-py3-none-any.whl']

    def test_no_wheel_of_the_version(self, index):
        index.add('demo', '1.0')

        with pytest.raises(WheelNotFoundError):
            wheels_of(index.links('demo'), 'demo', '2.0')


class TestDecisionText:
    """The line of the report."""

    def test_pip_with_the_evidence(self):
        decision = decide(
            'demo', '1.0', 'http://index.example/simple/demo/',
            [candidate('demo-1.0-py3-none-any.whl'),
             candidate('demo-1.0-cp38-cp38-win_amd64.whl')], TARGET)

        assert decision_text(decision) == (
            'demo==1.0 -> pip (compiled-wheel): demo-1.0-cp38-cp38-win_amd64.whl, '
            'index="http://index.example/simple/demo/"')

    def test_pack_with_the_pure_wheel(self):
        decision = decide(
            'demo', '1.0', '', [candidate('demo-1.0-py3-none-any.whl')], TARGET)

        assert decision_text(decision) == (
            'demo==1.0 -> pack (pure-only): demo-1.0-py3-none-any.whl')
