import subprocess
import sys

import pytest

from alasio.ext.backport import fix_py37_subprocess_communicate


@pytest.mark.skipif(
    sys.platform != 'win32' or sys.version_info[:2] != (3, 7),
    reason="Patch only applies to Windows Python 3.7"
)
@pytest.fixture(scope="module", autouse=True)
def apply_patch():
    """
    Automatically apply the patch before all tests start.
    """
    fix_py37_subprocess_communicate()
    yield


@pytest.mark.skipif(
    sys.platform != 'win32' or sys.version_info[:2] != (3, 7),
    reason="Patch only applies to Windows Python 3.7"
)
class TestSubprocessPatch:

    def test_patch_is_applied(self):
        """
        Test: Check if subprocess.Popen._communicate becomes our function after running the patch function
        """
        # 1. Get the _communicate method in the Popen class
        # Note: In Python 3, accessing class methods directly will get a function object
        current_method = subprocess.Popen._communicate

        # 2. Verify function name
        # The __name__ of the native method is "_communicate"
        # The function name defined in our closure is "_communicate_fixed"
        assert current_method.__name__ == "_communicate_fixed", \
            "Patch not applied successfully, current method name is still the native _communicate"

        print("\n[Check] Patch successfully applied. Function name is '_communicate_fixed'.")

    def test_communicate_execution(self):
        """
        Test: Verify if the replaced communicate method can execute commands normally and capture output
        """
        test_str = "hello_world"

        # 1. Execute a command normally (cmd /c echo ...)
        # Generate stdout and stderr at the same time
        cmd = f'cmd /c "echo {test_str} && echo {test_str}_err 1>&2"'

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True
        )

        # 2. Call communicate (this will actually call our _communicate_fixed)
        stdout, stderr = process.communicate()

        current_method = subprocess.Popen._communicate
        assert current_method.__name__ == "_communicate_fixed", \
            "Patch not applied successfully, current method name is still the native _communicate"

        # 2. Verify functionality is normal
        assert process.returncode == 0
        assert test_str in stdout
        assert f"{test_str}_err" in stderr

        print(f"\n[Check] Execution successful.")
        print(f"Stdout captured: {stdout.strip()}")
        print(f"Stderr captured: {stderr.strip()}")

    def test_empty_output_edge_case(self):
        """
        Test: Verify that the new function does not report an error when there is no output (this is a potential
        scenario for triggering the original bug)
        """
        # Execute a command that does not produce any output
        cmd = 'cmd /c "exit 0"'

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True
        )

        # In the original bug, if the stderr pipe is open but the list is empty, it will crash under certain conditions
        # Here we verify that it can safely return None or an empty string
        stdout, stderr = process.communicate()

        assert process.returncode == 0

        # Verify that the code does not throw an IndexError
        print("\n[Check] Empty output case passed without IndexError.")
