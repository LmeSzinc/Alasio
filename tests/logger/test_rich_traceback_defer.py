"""
The rich traceback patches must not run when the logger is imported.

patch_rich_traceback_extract() and patch_rich_traceback_links() patch
rich.traceback, so calling them imports it: that drags the whole rich renderer
stack in (rich.pretty, rich.columns, pygments.lexers, rich.syntax, about 100ms
and 150 modules). Every process imports the logger, including the ones that
never log an exception, so the patches are applied in rich_formatter() instead
of at module level.

The tests run a fresh interpreter on purpose: the module cache of the test
session already holds rich, so the same assertion in-process would pass
whatever the logger does.
"""
import json
import os
import subprocess
import sys

import pytest

from alasio.ext.env import ALASIO_ROOT

# Import the logger only, then report whether rich was touched
CHILD_IMPORT_ONLY = """
import json, sys
import alasio.logger  # noqa: F401
print(json.dumps({'rich_traceback_loaded': 'rich.traceback' in sys.modules}))
"""

# Format one traceback, then report the loaded modules and the applied patches
CHILD_FORMAT = """
import json, sys
from alasio.logger.logger import rich_formatter
try:
    raise ValueError('deferred patch probe')
except ValueError:
    exc_info = sys.exc_info()
exception_rich, exception_plain = rich_formatter(exc_info)
from rich.traceback import Traceback
print(json.dumps({
    'rich_traceback_loaded': 'rich.traceback' in sys.modules,
    'render_stack': Traceback._render_stack.__qualname__,
    'plain_has_traceback': 'Traceback' in exception_plain,
    'plain_has_message': 'ValueError: deferred patch probe' in exception_plain,
    'rich_has_message': 'ValueError' in exception_rich,
}))
"""


def run_child(code):
    """
    Run a snippet in a fresh interpreter and return what it printed.

    Args:
        code (str): Python source, it must print one JSON object as its last line

    Returns:
        dict: The decoded JSON object
    """
    env = os.environ.copy()
    root = os.path.normpath(ALASIO_ROOT)
    existing = env.get('PYTHONPATH')
    env['PYTHONPATH'] = root + (os.pathsep + existing if existing else '')

    proc = subprocess.run(
        [sys.executable, '-c', code],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1])


@pytest.fixture(scope='module')
def format_result():
    """
    Returns:
        dict: What the formatting child reported, spawned once for the whole module
    """
    return run_child(CHILD_FORMAT)


class TestRichTracebackDefer:
    def test_import_logger_does_not_load_rich_traceback(self):
        """Importing the logger must not pay for the rich renderer stack."""
        result = run_child(CHILD_IMPORT_ONLY)
        assert result['rich_traceback_loaded'] is False

    def test_formatting_loads_rich_traceback(self, format_result):
        """The first formatted traceback loads rich, it is only deferred."""
        assert format_result['rich_traceback_loaded'] is True

    def test_formatting_applies_the_patches(self, format_result):
        """Both patches still reach rich.traceback before it renders anything."""
        assert format_result['render_stack'] == \
            'patch_rich_traceback_links.<locals>.render_stack_patched'

    def test_formatting_still_renders_the_traceback(self, format_result):
        """Deferring the patches must not change the rendered output."""
        assert format_result['plain_has_traceback'] is True
        assert format_result['plain_has_message'] is True
        assert format_result['rich_has_message'] is True
