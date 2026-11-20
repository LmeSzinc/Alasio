"""
The two steps a git-over-cdn pack artifact is made of.

The server never computes a delta: `pack-objects` decides the object set and
the delta encoding, and the idx is written from the bytes of that pack. What
lives here is the process handling around git, nothing about the pack format.

Both steps work on bytes, not on files: `pack()` runs `pack-objects --stdout`
and answers the pack, and `idx()` answers its index through `GenIdx.pack_to_idx`
(the Python writer, byte for byte the idx git writes). An artifact is therefore
built in memory and reaches the disk once, as the zip a client downloads, which
is the only write of a build and the only file an interrupted run can leave.

Usage:
    tool = GitPackTool(repo)
    pack = tool.pack(want=latest, have=old)
    idx = tool.idx(pack)
"""

from alasio.ext.concurrent.cmd import CmdlineError, run_cmd
from alasio.git.stage.genidx import GenIdx
from alasio.logger import logger

# Seconds one command may run. A 30MB pack of the largest repo of the server
# takes about a second; this only catches a command that hangs
PACK_TIMEOUT = 900

# Value of pack.threads, 0 to leave git at its own default (one per core)
DEFAULT_THREADS = 0


class GitPackError(Exception):
    """
    Raised when a git command of the pack build fails or times out.

    The stderr of git is kept verbatim: it is the only thing that tells a
    missing object from a full disk, and the operator reads it as it is.
    """

    def __init__(self, message, cmd, returncode=0, stderr=''):
        """
        Args:
            message (str): What failed, in the words of the caller
            cmd (list[str]): The command that failed
            returncode (int): Its exit code, 0 when it never ran
            stderr (str): What git wrote on its error output
        """
        self.message = message
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(self.message)

    def __str__(self):
        text = f'{self.message}, cmd={" ".join(self.cmd)}'
        if self.returncode:
            text += f', returncode={self.returncode}'
        if self.stderr:
            text += f', stderr={self.stderr}'
        return text


class GitPackTool:
    """
    The pack and the idx of one version pair, computed by git.

    The repository is only read: `pack-objects --stdout` writes the pack to a
    pipe, and the idx is derived from those bytes, so a build writes no file and
    nothing in the object store.

    Usage:
        tool = GitPackTool(repo)
        pack = tool.pack(want=latest, have=old)
        idx = tool.idx(pack)
    """

    def __init__(self, repo, threads=DEFAULT_THREADS, timeout=PACK_TIMEOUT):
        """
        Args:
            repo (str | PathStr): Folder of the clone, passed as `git -C repo`
            threads (int): Value of pack.threads, 0 leaves git's default.
                Defaults to DEFAULT_THREADS
            timeout (int | float): Seconds one command may run. Defaults to
                PACK_TIMEOUT
        """
        self.repo = repo
        self.threads = threads
        self.timeout = timeout

    def _command(self, *args):
        """
        The command line of one git call.

        Args:
            *args (str): Subcommand and its arguments

        Returns:
            list[str]: The command, never run through a shell
        """
        cmd = ['git', '-C', str(self.repo)]
        if self.threads:
            # a server runs several channels, and git would otherwise take
            # every core for one pack
            cmd += ['-c', f'pack.threads={self.threads}']
        cmd += [str(arg) for arg in args]
        return cmd

    def _run(self, cmd, what, stdin=b''):
        """
        Run a git command of the build.

        Args:
            cmd (list[str]): The command
            what (str): Name of the step, for the message of a failure
            stdin (bytes): Content of the standard input

        Returns:
            bytes: What the command wrote on its standard output. The output is
                binary and never stripped: a pack read as text, or trimmed of
                its trailing bytes, is a corrupt pack

        Raises:
            GitPackError: If the command fails, is not installed, or times out
        """
        logger.debug(f'Git command: {" ".join(cmd)}')
        try:
            result = run_cmd(cmd, timeout=self.timeout, text=False, strip=False, input=stdin)
        except CmdlineError as e:
            stderr = e.stderr or b''
            if isinstance(stderr, bytes):
                stderr = stderr.decode('utf-8', 'replace')
            message = 'timed out' if 'timed out' in e.msg else 'failed'
            raise GitPackError(f'{what} {message}', cmd=cmd,
                               returncode=e.returncode, stderr=stderr.strip()) from None
        return result.stdout

    def pack(self, want, have, thin=False):
        """
        The pack of one version pair: `want` minus `have`.

        The command is the one `git-upload-pack` runs internally: `rev-list`
        of the range decides the objects, and git decides the delta encoding.
        The pack comes back on the standard output, so a build writes no
        temporary file at all.

        The object set is the range and nothing else. `--no-sparse` is passed
        rather than left to the default, which differs between git versions and
        follows `pack.useSparse` of the operator: the sparse edge walk marks
        what the client has by path, so a blob behind a rename is packed again
        although the client holds it. The flag costs a walk of the trees of the
        edge commit, and buys an object set that does not move with the git
        that builds the artifact.

        Args:
            want (str): Commit the client updates to, the latest version
            have (str): Commit the client already has, the old version
            thin (bool): True to allow a delta base outside the pack. Defaults
                to False: a client reads the pack and the idx from its disk,
                and git resolves a ref-delta base only inside the same pack,
                so a thin pack is not readable there

        Returns:
            bytes: The pack, from its header to its trailing checksum

        Raises:
            GitPackError: If git fails or times out
        """
        cmd = self._command('pack-objects', '--stdout', '--revs', '--delta-base-offset', '--no-sparse')
        if thin:
            cmd.append('--thin')
        # --revs reads revision expressions, not a list of object ids
        stdin = f'{want}\n^{have}\n'.encode()
        pack = self._run(cmd, 'pack-objects', stdin=stdin)
        if pack[:4] != b'PACK':
            raise GitPackError(
                f'pack-objects answered {len(pack)} bytes that do not start with PACK',
                cmd=cmd, stderr=pack[:32].hex())
        return pack

    def idx(self, pack_data):
        """
        The index of a pack, from the bytes of the pack.

        `GenIdx.pack_to_idx` resolves every delta chain of the pack and writes
        the same idx git does, byte for byte, with no file and no repository
        behind it: the pack read back must be one git can read, which the same
        function checks in strict mode.

        Args:
            pack_data (bytes): The pack, from `pack()`

        Returns:
            bytes: The idx file, including its own trailing sha1

        Raises:
            PackNotSelfContained: If the pack refers to a base it does not hold
            PackBroken: If the pack is not readable as a pack
        """
        return GenIdx('').pack_to_idx(pack_data)
