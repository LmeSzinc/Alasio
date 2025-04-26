import sys

import psutil
from psutil._common import get_procfs_path, open_text
from psutil import _psplatform as psplatform


# proc.py is an alternative of psutil.
# It's 10+ times faster by directly access psutil's C bindings

# psutil.pids()
def pids() -> "list[int]":
    # [MODIFIED] No global variable _LOWEST_PID
    return sorted(psplatform.pids())


# psutil.Process.cmdline()
if psutil.LINUX:
    import psutil._psutil_linux as cext


    def cmdline(pid):
        procfs_path = get_procfs_path()
        with open_text("%s/%s/cmdline" % (procfs_path, pid)) as f:
            data = f.read()
        if not data:
            # [MODIFIED] No raises for zombie process because error processes will be ignored
            # self._raise_if_zombie()
            return []
        # 'man proc' states that args are separated by null bytes '\0'
        # and last char is supposed to be a null byte. Nevertheless
        # some processes may change their cmdline after being started
        # (via setproctitle() or similar), they are usually not
        # compliant with this rule and use spaces instead. Google
        # Chrome process is an example. See:
        # https://github.com/giampaolo/psutil/issues/1179
        sep = '\x00' if data.endswith('\x00') else ' '
        if data.endswith(sep):
            data = data[:-1]
        cmdline = data.split(sep)
        # Sometimes last char is a null byte '\0' but the args are
        # separated by spaces, see: https://github.com/giampaolo/psutil/
        # issues/1179#issuecomment-552984549
        if sep == '\x00' and len(cmdline) == 1 and ' ' in data:
            cmdline = data.split(' ')
        return cmdline

elif psutil.WINDOWS:
    import psutil._psutil_windows as cext


    def cmdline(pid):
        # [MODIFIED] No permission fallback on > WINDOWS_8_1 because we don't need that precise
        # [MODIFIED] No PY2 support
        return cext.proc_cmdline(pid, use_peb=True)

elif psutil.MACOS:
    import psutil._psutil_osx as cext


    def cmdline(pid):
        return cext.proc_cmdline(pid)

elif psutil.BSD:
    import psutil._psutil_bsd as cext


    def cmdline(pid):
        if psutil.OPENBSD and pid == 0:
            return []  # ...else it crashes
        elif psutil.NETBSD:
            # XXX - most of the times the underlying sysctl() call on
            # NetBSD and OpenBSD returns a truncated string. Also
            # /proc/pid/cmdline behaves the same so it looks like this
            # is a kernel bug.

            # [MODIFIED] No try except because error processes will be ignored
            return cext.proc_cmdline(pid)

        else:
            return cext.proc_cmdline(pid)


elif psutil.SUNOS:
    import psutil._psutil_sunos as cext


    def cmdline(pid):
        # [MODIFIED] Flatten _proc_name_and_args
        procfs_path = get_procfs_path()
        proc_name_and_args = cext.proc_name_and_args(pid, procfs_path)
        return proc_name_and_args[1].split(' ')


elif psutil.AIX:
    import psutil._psutil_aix as cext


    def cmdline(pid):
        return cext.proc_args(pid)

else:  # pragma: no cover
    raise NotImplementedError('platform %s is not supported' % sys.platform)


def iter_process() -> "tuple[int, list[str]]":
    """
    Iter all running processes

    FYI, to terminate or kill the process, do:
        psutil.Process(pid).terminate()
        psutil.Process(pid).kill()

    Yields:
        int: pid
        list[str]: cmdline, and it's guaranteed to have at least one element
            Don't forget to normpath if you need to check cmdline
    """
    if psutil.WINDOWS:
        # Since this is a one-time-usage, we access psutil._psplatform.Process directly
        # to bypass the call of psutil.Process.is_running().
        # This only costs about 0.017s.
        # If you do psutil.process_iter(['pid', 'cmdline']) it will take over 1s
        for pid in pids():
            # 0 and 4 are always represented in taskmgr and process-hacker
            if pid == 0 or pid == 4:
                continue
            try:
                # This would be fast on psutil<=5.9.8 taking overall time 0.027s
                # but taking 0.39s on psutil>=6.0.0
                cmd = cmdline(pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess, IndexError, OSError):
                # psutil.AccessDenied
                # NoSuchProcess: process no longer exists (pid=xxx)
                # ProcessLookupError: [Errno 3] assume no such process (originated from psutil_pid_is_running -> 0)
                # OSError: [WinError 87] 参数错误。: '(originated from ReadProcessMemory)'
                continue

            # Validate cmdline
            if not cmd:
                continue
            try:
                exe = cmd[0]
            except IndexError:
                continue
            # \??\C:\Windows\system32\conhost.exe
            if exe.startswith(r'\??'):
                continue
            yield pid, cmd
    else:
        for pid in pids():
            try:
                cmd = cmdline(pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess, IndexError, OSError):
                continue

            # Validate cmdline
            if not cmd:
                continue
            try:
                cmd[0]
            except IndexError:
                continue
            yield pid, cmd
