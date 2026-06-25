from typing import List, Literal, Optional, Union, overload


class CmdlineError(Exception):
    def __init__(self, cmd, msg, returncode=0, stdout=None, stderr=None, e=None):
        self.cmd: "List[str]" = cmd
        self.msg: str = msg
        self.returncode: "Optional[int]" = returncode
        self.stdout: "Optional[str]" = stdout
        self.stderr: "Optional[str]" = stderr
        self.e = e
        super().__init__(self.msg)

    def __repr__(self):
        return f'{self.__class__.__name__}(returncode={self.returncode}, msg="{self.msg}")'


class _CmdlineResultBase:
    cmd: "list[str]"
    returncode: int
    stdout: "Optional[str]"
    stderr: "Optional[str]"

    def __init__(self, cmd, returncode, stdout=None, stderr=None):
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __repr__(self):
        return f'{self.__class__.__name__}(returncode={self.returncode})'

    def check_returncode(self):
        """Raise CalledProcessError if the exit code is non-zero."""
        if self.returncode:
            import subprocess
            error = subprocess.CalledProcessError(self.returncode, self.cmd, self.stdout, self.stderr)
            raise CmdlineError(
                msg=str(error), cmd=self.cmd, returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)
        return self


class CmdlineResultStr(_CmdlineResultBase):
    stdout: "Optional[str]"
    stderr: "Optional[str]"


class CmdlineResultBytes(_CmdlineResultBase):
    stdout: "Optional[bytes]"
    stderr: "Optional[bytes]"


def parse_result(data, text=True, strip=True):
    """
    Convert bytes or None to string and optionally strip it.

    Args:
        data (str | bytes | None): Data to convert
        text (bool):
        strip (bool):

    Returns:
        str | None: Converted string or None
    """
    if data is None:
        return None
    if text:
        if isinstance(data, bytes):
            data = data.decode('utf-8', 'replace')
    if strip:
        data = data.strip()
    return data


@overload
def run_cmd(
        cmd: "List[str]",
        timeout: "Union[int, float]" = 10,
        text: "Literal[True]" = True,
        strip: bool = True,
        **kwargs,
) -> CmdlineResultStr: ...


@overload
def run_cmd(
        cmd: "List[str]",
        timeout: "Union[int, float]" = 10,
        text: "Literal[False]" = False,
        strip: bool = True,
        **kwargs,
) -> CmdlineResultBytes: ...


def run_cmd(
        cmd: "List[str]",
        timeout: "Union[int, float]" = 10,
        text: bool = True,
        strip: bool = True,
        encoding='utf-8',
        errors='replace',
        check=True,
        **kwargs,
) -> "Union[CmdlineResultStr, CmdlineResultBytes]":
    """
    Args:
        cmd (list[str]):
        timeout (int | float): Timeout in seconds
        text (bool): True to return in str, False to return in text
        strip (bool): Whether to strip() output, stdout, stderr. Defaults to True.
        encoding:
        errors:
        check:

    Returns:
        CmdlineResultStr | CmdlineResultBytes:
    """
    if not cmd:
        raise CmdlineError(msg='Empty command list', cmd=cmd, e=ValueError('Empty command list'))
    cmd = [str(c).strip() for c in cmd]
    if not cmd[0]:
        raise CmdlineError(msg='Empty executable', cmd=cmd, e=ValueError('Empty executable'))

    if not text:
        encoding = None
        errors = None
    kwargs.pop('capture_output', None)
    kwargs.pop('shell', None)

    import subprocess
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=text,
            encoding=encoding,
            errors=errors,
            timeout=timeout,
            check=check,
            shell=False,
            **kwargs,
        )
        stdout = parse_result(result.stdout, text=text, strip=strip)
        stderr = parse_result(result.stderr, text=text, strip=strip)
        if text:
            return CmdlineResultStr(cmd=cmd, returncode=result.returncode, stdout=stdout, stderr=stderr)
        else:
            return CmdlineResultBytes(cmd=cmd, returncode=result.returncode, stdout=stdout, stderr=stderr)

    except FileNotFoundError as e:
        raise CmdlineError(
            msg=f'Command not found: {cmd[0]}',
            cmd=cmd, e=e,
        )
    except PermissionError as e:
        raise CmdlineError(
            msg=f'Permission denied when executing: {cmd}',
            cmd=cmd, e=e,
        )
    except subprocess.CalledProcessError as e:
        stdout = parse_result(e.stdout, text=text, strip=strip)
        stderr = parse_result(e.stderr, text=text, strip=strip)
        raise CmdlineError(
            msg=f'Command failed with return code {e.returncode}: {cmd}',
            cmd=cmd, returncode=e.returncode, stdout=stdout, stderr=stderr, e=e,
        )
    except subprocess.TimeoutExpired as e:
        stdout = parse_result(e.stdout, text=text, strip=strip)
        stderr = parse_result(e.stderr, text=text, strip=strip)
        raise CmdlineError(
            msg=f'Command timed out after {timeout}s: {cmd}',
            cmd=cmd, stdout=stdout, stderr=stderr, e=e,
        )
    except Exception as e:
        raise CmdlineError(
            msg=f'Unexpected error during execution: {str(e)}, cmd={cmd}',
            cmd=cmd, e=e,
        )
