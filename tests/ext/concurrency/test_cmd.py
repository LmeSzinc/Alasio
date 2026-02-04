import sys
from unittest.mock import patch

import pytest

from alasio.ext.concurrent.cmd import CmdlineError, run_cmd

PYTHON = sys.executable


def test_run_cmd_success():
    """
    Test successful command execution.
    """
    res = run_cmd([PYTHON, "-c", "print('hello')"])
    assert res == "hello"


def test_run_cmd_strip():
    """
    Test that output is stripped of leading and trailing whitespace.
    """
    res = run_cmd([PYTHON, "-c", "print('  hello  ')"])
    assert res == "hello"


def test_run_cmd_no_strip():
    """
    Test that output is not stripped when strip=False.
    """
    res = run_cmd([PYTHON, "-c", "import sys; sys.stdout.write('  hello  \\n')"], strip=False)
    assert res == "  hello  \n"


def test_run_cmd_empty_list():
    """
    Test that providing an empty command list raises CmdlineError.
    """
    with pytest.raises(CmdlineError) as exc:
        run_cmd([])
    assert "Empty command list" in str(exc.value)


def test_run_cmd_empty_executable():
    """
    Test that providing a command with an empty executable raises CmdlineError.
    """
    with pytest.raises(CmdlineError) as exc:
        run_cmd(["", "-c", "print('hello')"])
    assert "Empty executable" in str(exc.value)


def test_run_cmd_not_found():
    """
    Test that executing a non-existent command raises CmdlineError.
    """
    with pytest.raises(CmdlineError) as exc:
        run_cmd(["non_existent_command_12345"])
    assert "Command not found" in str(exc.value)


def test_run_cmd_failed():
    """
    Test that a command returning a non-zero exit code raises CmdlineError.
    """
    with pytest.raises(CmdlineError) as exc:
        run_cmd([PYTHON, "-c", "import sys; print('stdout_msg'); sys.stderr.write('error_msg\\n'); sys.exit(1)"])
    assert "Command failed with return code 1" in str(exc.value)
    assert exc.value.returncode == 1
    assert exc.value.stdout == "stdout_msg"
    assert exc.value.stderr == "error_msg"


def test_run_cmd_failed_no_strip():
    """
    Test that a command returning a non-zero exit code raises CmdlineError with unstripped output.
    """
    with pytest.raises(CmdlineError) as exc:
        run_cmd([PYTHON, "-c", "import sys; sys.stdout.write(' stdout_msg '); sys.stderr.write(' error_msg '); sys.exit(1)"], strip=False)
    assert exc.value.stdout == " stdout_msg "
    assert exc.value.stderr == " error_msg "


def test_run_cmd_timeout():
    """
    Test that a command exceeding the timeout raises CmdlineError.
    """
    with pytest.raises(CmdlineError) as exc:
        # Use a command that sleeps longer than timeout after printing
        run_cmd([PYTHON, "-u", "-c", "import time; print('before_sleep', flush=True); time.sleep(2)"], timeout=0.5)
    assert "Command timed out" in str(exc.value)
    # Note: subprocess may or may not capture output before timeout depending on buffering and OS.
    # Using -u and flush=True helps.
    assert exc.value.stdout == "before_sleep"


def test_run_cmd_encoding_error():
    """
    Test that invalid UTF-8 output is handled with 'replace' strategy.
    """
    # Test 'replace' error handling in encoding
    # printing bytes that are not valid utf-8
    res = run_cmd([PYTHON, "-c", "import sys; sys.stdout.buffer.write(b'hello\\xffworld\\n')"])
    assert "hello" in res
    assert "world" in res
    # \xff should be replaced by \ufffd
    assert "\ufffd" in res


def test_run_cmd_unexpected_error():
    """
    Test that unexpected exceptions during execution are wrapped in CmdlineError.
    """
    with patch("subprocess.run", side_effect=RuntimeError("something went wrong")):
        with pytest.raises(CmdlineError) as exc:
            run_cmd([PYTHON, "-c", "print('hello')"])
        assert "Unexpected error during execution: something went wrong" in str(exc.value)
