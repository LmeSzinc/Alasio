from typing import Optional


class CmdlineError(Exception):
    def __init__(
            self,
            msg: str,
            returncode: Optional[int] = None,
            stdout: Optional[str] = None,
            stderr: Optional[str] = None,
            e: Optional[Exception] = None
    ):
        self.msg = msg
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.e = e
        super().__init__(self.msg)

    def __str__(self):
        return f"{self.msg} (ReturnCode: {self.returncode})"


def to_string(data, strip=True):
    """
    Convert bytes or None to string and optionally strip it.

    Args:
        data (str | bytes | None): Data to convert
        strip (bool):

    Returns:
        str | None: Converted string or None
    """
    if data is None:
        return None
    if isinstance(data, bytes):
        data = data.decode('utf-8', 'replace')
    if strip:
        data = data.strip()
    return data


def run_cmd(cmd, timeout=10, strip=True):
    """
    Args:
        cmd (list[str]):
        timeout (int | float): Timeout in seconds
        strip (bool): Whether to strip() output, stdout, stderr. Defaults to True.

    Returns:
        str:
    """
    if not cmd:
        raise CmdlineError(msg='Empty command list', e=ValueError('Empty command list'))
    cmd = [str(c).strip() for c in cmd]
    if not cmd[0]:
        raise CmdlineError(msg='Empty executable', e=ValueError('Empty executable'))

    import subprocess
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            check=True,
            shell=False
        )
        return to_string(result.stdout, strip=strip)

    except FileNotFoundError as e:
        raise CmdlineError(
            msg=f'Command not found: {cmd[0]}',
            e=e,
        )
    except PermissionError as e:
        raise CmdlineError(
            msg=f'Permission denied when executing: {cmd}',
            e=e,
        )
    except subprocess.CalledProcessError as e:
        stdout = to_string(e.stdout, strip=strip)
        stderr = to_string(e.stderr, strip=strip)
        raise CmdlineError(
            msg=f'Command failed with return code {e.returncode}: {cmd}',
            returncode=e.returncode, stdout=stdout, stderr=stderr, e=e,
        )
    except subprocess.TimeoutExpired as e:
        stdout = to_string(e.stdout, strip=strip)
        stderr = to_string(e.stderr, strip=strip)
        raise CmdlineError(
            msg=f'Command timed out after {timeout}s: {cmd}',
            stdout=stdout, stderr=stderr, e=e,
        )
    except Exception as e:
        raise CmdlineError(
            msg=f'Unexpected error during execution: {str(e)}, cmd={cmd}',
            e=e,
        )
