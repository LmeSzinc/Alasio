"""
Shell update: replacing the electron archive of a packaged installation

The application carries the electron archive it needs inside the package
(``alasio/deploy_data/webapp/app.asar``); an installed copy keeps the one the
running electron loaded in ``resources/app.asar``. Replacing it takes a whole
chain relaunch: electron holds the archive while it runs and the swap needs
every process of the old chain gone, so no in-process update can do it.

The mechanism, split over four places:

1. this module compares the two archives and stages the package copy next to
   the installed one as ``new-app.asar``. The installed file is never touched
   from here -- only the updater replaces it, and only after the chain is down;
2. the handover of a flow starts ``deploy/updater.py`` with the identity of the
   three processes the updater has to outlive;
3. the backend asks the supervisor for the relaunch, the supervisor tells the
   host over an authenticated line, the host shuts the chain down;
4. the updater replaces the archive and starts the new chain.

Only a packaged electron hands this module an install context (the
``ALASIO_APP_*`` variables, see ``ShellTarget``). A development checkout, a
remote deployment served in a browser and a backend started by hand all report
``available == False`` and every step here is a no-op.

Nothing here parses the archive: the bytes of the package copy are the truth
and the only judgement is a digest comparison. A client that second-guessed the
content would need the server to describe it, and the server is the party that
shipped those bytes -- it could describe them wrongly just as easily as ship
them wrongly.
"""
import hashlib
import json
import os
import subprocess
import sys

import trio

from alasio.deploy.updater import start_time
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_write_stream
from alasio.logger import logger

# Install context written by electron (see ShellTarget)
ENV_ASAR = 'ALASIO_APP_ASAR'
ENV_EXE = 'ALASIO_APP_EXE'
ENV_CWD = 'ALASIO_APP_CWD'
ENV_ARGV = 'ALASIO_APP_ARGV'
# Identity of the host process, written by electron itself and inherited by
# every process of the chain. Nothing verifies it until the archive has to be
# replaced: the pid is a switch first, a target second.
ENV_ELECTRON_PID = 'ALASIO_APP_ELECTRON_PID'
# Input of the updater, written by spawn_updater() for one process only
ENV_RELAUNCH_TARGETS = 'ALASIO_RELAUNCH_TARGETS'
ENV_RELAUNCH_SHA256 = 'ALASIO_RELAUNCH_SHA256'
ENV_RELAUNCH_TIMEOUT = 'ALASIO_RELAUNCH_TIMEOUT'
ENV_RESUME_FILE = 'ALASIO_RESUME_FILE'
ENV_RESUME_TOKEN = 'ALASIO_RESUME_TOKEN'

# Name of the staged archive, next to the installed one (the updater derives
# the same path from ALASIO_APP_ASAR, this is the writer's side of it)
STAGED_NAME = 'new-app.asar'
# Layout of the package copy, see RepoWithStatics.static_files
BUNDLED_PARTS = ('alasio', 'deploy_data', 'webapp', 'app.asar')
# Path of the updater, started by path and never through -m so the package is
# not imported into a process that must survive its own installation
UPDATER_PARTS = ('alasio', 'deploy', 'updater.py')
# Seconds the updater waits for the old chain before it gives up (it starts
# nothing when the chain never came down)
RELAUNCH_TIMEOUT = 120
# Digest chunk size: the archive is a few hundred kilobytes, so the read is
# one or two calls instead of one allocation of the whole file
CHUNK_SIZE = 1 << 20


class ShellTarget:
    """
    The install context of a packaged application, read from the environment.

    Attributes:
        asar (str): Installed archive, ``<resources>/app.asar``
        exe (str): Absolute path of the application executable
        cwd (str): Working directory the host started in
        argv (list[str]): Arguments the host was started with (without the
            executable itself)
        electron_pid (int): Pid of the host process, as electron reported it
        src (str): Staged archive next to the installed one
    """

    __slots__ = ('asar', 'exe', 'cwd', 'argv', 'electron_pid', 'src')

    def __init__(self, asar, exe, cwd, argv, electron_pid):
        """
        Args:
            asar (str): Absolute path of the installed archive
            exe (str): Absolute path of the executable
            cwd (str): Working directory of the host
            argv (list[str]): Arguments of the host
            electron_pid (int): Pid of the host process
        """
        self.asar = asar
        self.exe = exe
        self.cwd = cwd
        self.argv = argv
        self.electron_pid = electron_pid
        self.src = os.path.join(os.path.dirname(asar), STAGED_NAME)

    @classmethod
    def from_environ(cls, environ):
        """
        Read the install context from the environment.

        Every variable is required and every value is validated here: a
        context that is only half present is no context at all, because a
        relaunch driven by half a context would start the wrong program with
        the wrong arguments.

        The existence of the host is not checked here: the pid is a switch for
        the whole chain (electron wrote it, every process inherited it), and the
        process behind it is asked about only when the archive actually has to
        be replaced (see ``ShellUpdate.pending`` and ``stage``). A backend
        started by hand with a stale pid therefore keeps working -- it just
        cannot update the archive, which is the honest answer.

        Args:
            environ (Mapping[str, str]): Environment to read

        Returns:
            ShellTarget | None: The context, None when this process does not
                run inside a packaged electron
        """
        asar = environ.get(ENV_ASAR, '')
        exe = environ.get(ENV_EXE, '')
        cwd = environ.get(ENV_CWD, '')
        if not (os.path.isabs(asar) and os.path.isabs(exe) and os.path.isabs(cwd)):
            return None
        if os.path.basename(asar) != 'app.asar':
            return None
        try:
            argv = json.loads(environ.get(ENV_ARGV, ''))
        except ValueError:
            return None
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            return None
        try:
            electron_pid = int(environ.get(ENV_ELECTRON_PID, ''))
        except ValueError:
            return None
        if electron_pid <= 0:
            return None
        return cls(asar, exe, cwd, argv, electron_pid)

    def host_start(self):
        """
        Read the kernel start marker of the host process.

        Returns:
            int | None: The marker, None when the pid names no process this
                user can describe (the session is over, the pid was recycled
                into something else, or the platform has no probe)
        """
        return self._marker_of(self.electron_pid)

    @staticmethod
    def _marker_of(pid):
        """
        Read the kernel start marker of a process.

        Args:
            pid (int): Process to read

        Returns:
            int | None: The marker, None when it cannot be read
        """
        try:
            return start_time(pid)
        except (OSError, NotImplementedError):
            return None

    def describe(self):
        """
        Returns:
            str: One line naming the context, for the log
        """
        return f'{self.asar} (exe={self.exe}, host pid={self.electron_pid})'


class StagedAsar:
    """
    A package archive waiting to be installed.

    Attributes:
        src (str): ``new-app.asar`` next to the installed archive
        dst (str): The installed archive the updater replaces
        sha256 (str): Digest of the staged bytes, the value the updater
            verifies before it touches anything
    """

    __slots__ = ('src', 'dst', 'sha256')

    def __init__(self, src, dst, sha256):
        """
        Args:
            src (str): Staged archive path
            dst (str): Installed archive path
            sha256 (str): Digest of the staged bytes
        """
        self.src = src
        self.dst = dst
        self.sha256 = sha256


class ShellUpdate:
    """
    The shell update of this backend process: the install context, the digest
    comparison, the staged archive and the updater process

    The context is configured once per process (``configure``) and read by the
    flows afterwards. Besides the context, the only state is the two cached
    digests (``installed_digest`` / ``bundled_digest``): the staged archive
    itself is a file, so a second call of ``stage()`` in another flow finds it
    and reuses it instead of writing it again.
    """

    def __init__(self):
        # ShellTarget | None: None when this process has no install context
        self.target = None

    def configure(self, environ=None):
        """
        Read the install context of this process.

        Args:
            environ (Mapping[str, str]): Environment to read, os.environ when
                omitted. Defaults to None.

        Returns:
            bool: True when the context is complete
        """
        environ = os.environ if environ is None else environ
        self.target = ShellTarget.from_environ(environ)
        # the digests describe the files of the context that was just read: a
        # second configure may point somewhere else entirely
        cached_property.pop(self, 'installed_digest')
        cached_property.pop(self, 'bundled_digest')
        if self.target is None:
            # A development checkout has the host pid (electron writes it on
            # every launch) and none of the archive variables; a broken chain
            # has some of them. Only the second is worth a warning.
            present = [
                name for name in (ENV_ASAR, ENV_EXE, ENV_CWD, ENV_ARGV) if environ.get(name)]
            if present:
                logger.warning('[Shell] The install context is incomplete or malformed: '
                               'no shell update')
            else:
                logger.info('[Shell] No install context: the electron archive is not managed '
                            'by this process')
            return False
        logger.info(f'[Shell] Install context: {self.target.describe()}')
        return True

    def reset(self):
        """Drop the context and the memoized digests (tests: a fresh process)"""
        self.target = None
        cached_property.pop(self, 'installed_digest')
        cached_property.pop(self, 'bundled_digest')

    @property
    def available(self):
        """
        Returns:
            bool: Whether this process may replace the electron archive
        """
        return self.target is not None

    @property
    def bundled(self):
        """
        Returns:
            PathStr: The archive the package carries
        """
        path = env.ALASIO_ROOT
        for part in BUNDLED_PARTS:
            path = path.joinpath(part)
        return path

    @staticmethod
    def digest(file):
        """
        sha256 of a file.

        Args:
            file (str): File to read

        Returns:
            str | None: Lowercase hex digest, None when the file cannot be read
        """
        digest = hashlib.sha256()
        try:
            with open(file, 'rb') as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError:
            return None
        return digest.hexdigest()

    @cached_property
    def installed_digest(self):
        """
        Digest of the installed archive.

        Only the updater replaces that file, and it waits for this process to
        exit before it does, so the digest of the installed archive is a
        constant of the process lifetime: a cached property is the whole
        memoization it needs.

        Returns:
            str | None: Lowercase hex digest, None when the file cannot be read
        """
        return self.digest(self.target.asar)

    @cached_property
    def bundled_digest(self):
        """
        Identity and digest of the archive the package carries.

        Unlike the installed one, this file belongs to a tree an update flow
        can rewrite while the backend runs, so the answer is tied to the file
        it was read from: the tuple carries the stat taken before the read, and
        ``bundled_now`` drops the cached value as soon as that stat moves.

        Returns:
            tuple | None: ``(st_mtime_ns, st_size, sha256)``, None when the
                archive cannot be read
        """
        try:
            st = os.stat(self.bundled)
        except OSError:
            return None
        digest = self.digest(self.bundled)
        if digest is None:
            return None
        return st.st_mtime_ns, st.st_size, digest

    def bundled_now(self):
        """
        Digest of the packaged archive as the file is right now.

        The cached ``bundled_digest`` is trusted only while the file still has
        the stat it was read under; a package tree the update flow rewrote in
        the meantime makes the cached value stale, so it is dropped and the
        digest is read again.

        Returns:
            str | None: Lowercase hex digest, None when the file cannot be read
        """
        cached = cached_property.get(self, 'bundled_digest', None)
        if cached is not None:
            try:
                st = os.stat(self.bundled)
            except OSError:
                moved = True
            else:
                moved = (st.st_mtime_ns, st.st_size) != cached[:2]
            if not moved:
                return cached[2]
            cached_property.pop(self, 'bundled_digest')
        cached = self.bundled_digest
        return None if cached is None else cached[2]

    def pending(self):
        """
        Whether the installed archive differs from the one the package carries.

        Read-only: this is the question the update flow asks before it decides
        that there is nothing to do, and the answer must not write anything.

        The digests are cached properties, so the two flows that ask cost one
        read of each file in the process lifetime (a package archive that moved
        is read again), not one read per call. The host is checked on every
        call and never cached: a process can die between two calls, and the
        answer that matters is the one at handover time.

        Returns:
            bool: True when a relaunch would install a different archive
        """
        target = self.target
        if target is None:
            return False
        bundled = self.bundled_now()
        if bundled is None:
            # the package carries no archive (a build without the webapp): this
            # installation is not maintained by the updater
            return False
        if self.installed_digest == bundled:
            return False
        return self._host_ready()

    def _host_ready(self):
        """
        Whether the host process of the context is there to be waited for.

        Returns:
            bool: True when the kernel can describe the pid
        """
        target = self.target
        if target.host_start() is not None:
            return True
        logger.warning(f'[Shell] The electron process of pid {target.electron_pid} does not '
                       f'exist: the archive cannot be replaced, keeping the installed one')
        return False

    @staticmethod
    def copy_with_digest(src, dst):
        """
        Copy a file while hashing it, one chunk at a time.

        The archive is never held in memory: a chunk is read, hashed and
        written before the next one is read, so staging costs the same whether
        the archive is 400 KB or 40 MB. The write is atomic (a temporary file
        next to the target is renamed over it), so a failure leaves whatever
        was there before.

        Args:
            src (str): File to read
            dst (str): File to write

        Returns:
            str: Lowercase hex digest of the bytes written

        Raises:
            OSError: The read or the write failed
        """
        digest = hashlib.sha256()

        def chunks():
            with open(src, 'rb') as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
                    yield chunk

        atomic_write_stream(dst, chunks())
        return digest.hexdigest()

    def stage(self):
        """
        Write the package archive next to the installed one.

        This is the only place the bytes travel: the whole chain is still alive
        here, so a failure (read only folder, full disk) is recoverable -- the
        caller falls back to a plain backend restart. What the copy does is pin
        the content: the digest it returns belongs to the very bytes that are
        now on disk, and the updater, which starts after the chain came down,
        only has to verify and move that file. Reading the package archive
        later would leave the updater depending on a tree a concurrent update
        may have rewritten.

        The copy is streamed (``copy_with_digest``) and so is the digest that
        decides whether it is needed at all: the archive is never loaded into
        memory.

        Idempotent: a staged archive that already holds the same bytes is
        reused, so the two flows that ask (a startup that found a pending
        update, then the user restart that serves it) write once.

        Returns:
            StagedAsar | None: What the updater has to install, None when
                nothing has to be installed or this process may not install it

        Raises:
            OSError: The staged archive cannot be written (read only folder,
                full disk): the caller falls back to a plain restart
        """
        target = self.target
        if target is None:
            return None
        bundled = self.bundled_now()
        if bundled is None:
            # no archive in the package, or it cannot be read: nothing to
            # install, and that is not an error of this installation
            logger.warning(f'[Shell] The package archive is not readable, no shell update: '
                           f'{self.bundled}')
            return None
        if self.installed_digest == bundled:
            logger.info('[Shell] The installed archive is the one of the package')
            return None
        if not self._host_ready():
            # the file would be staged for a chain that can never come down
            return None
        if self.digest(target.src) == bundled:
            logger.info(f'[Shell] The staged archive is ready: {target.src}')
            return StagedAsar(target.src, target.asar, bundled)
        # the digest of the copy is the truth, not the one read a moment ago:
        # a package tree rewritten between the two reads is installed as it is
        # now, and the updater verifies exactly this value
        digest = self.copy_with_digest(self.bundled, target.src)
        logger.info(f'[Shell] Staged the package archive: {target.src} (sha256={digest})')
        return StagedAsar(target.src, target.asar, digest)

    async def pending_async(self):
        """
        ``pending()`` off the event loop (the digests read files).

        Returns:
            bool: True when a handover would install a different archive
        """
        return await trio.to_thread.run_sync(self.pending)

    async def stage_async(self):
        """
        ``stage()`` off the event loop, with its failure handled here.

        A staging failure (read only folder, full disk) is not an error of the
        handover: the backend restarts alone and the next flow tries the
        archive again. The decision belongs to this module because it is the
        shell update's own recovery, not a flow's.

        Returns:
            StagedAsar | None: What the updater has to install, None when there
                is nothing to install or the archive cannot be staged
        """
        try:
            return await trio.to_thread.run_sync(self.stage)
        except OSError as e:
            logger.warning(f'[Shell] Cannot stage the electron archive, restarting the backend only: {e}')
            return None

    def targets(self):
        """
        The processes the updater waits for, as the updater reads them.

        The markers are kernel counters (``updater.start_time``), so a pid the
        system recycled while the updater waited cannot pass for the process
        that was asked about. A marker the platform cannot report is left out:
        the pid alone is a weaker identity, and waiting longer is the safe
        direction.

        Returns:
            dict: ``{name: {'pid': int, 'start': int}}``
        """
        target = self.target
        return {
            'backend': self._identity(os.getpid()),
            'supervisor': self._identity(os.getppid()),
            'electron': self._entry(target.electron_pid, target.host_start()),
        }

    @staticmethod
    def _entry(pid, marker):
        """
        Args:
            pid (int): Process to describe
            marker (int | None): Kernel start marker, left out when None

        Returns:
            dict: {'pid': pid} plus 'start' when the marker is known
        """
        entry = {'pid': pid}
        if marker is not None:
            entry['start'] = marker
        return entry

    @staticmethod
    def _identity(pid):
        """
        Args:
            pid (int): Process to describe

        Returns:
            dict: {'pid': pid} plus 'start' when the platform reports it
        """
        return ShellUpdate._entry(pid, ShellTarget._marker_of(pid))

    def updater_script(self):
        """
        Returns:
            PathStr: Absolute path of deploy/updater.py
        """
        path = env.ALASIO_ROOT
        for part in UPDATER_PARTS:
            path = path.joinpath(part)
        return path

    def spawn_updater(self, staged, credential, resume_file):
        """
        Start the updater, detached from this chain.

        The command line carries nothing, the environment carries everything:
        a credential in argv would be visible in the process list, and the
        paths would have to be parsed back. The three standard descriptors go
        to the null device -- the process outlives the chain that started it,
        so its inherited pipes have no reader left (a full pipe buffer would
        block it forever).

        The staged archive and its digest travel with the process: the updater
        has nothing to copy, only to check the file this chain pinned and to
        rename it over the installed one.

        Detaching keeps the updater out of the process group that a terminal
        signal takes down, but it does not protect it from a tree kill: on
        every platform a tree kill walks the parent chain, so the relaunch path
        of the host must never do one (see the electron side).

        Args:
            staged (StagedAsar): What the updater installs
            credential (str): Resume credential of the publication
            resume_file (str): Resume file, the GO signal of the updater

        Returns:
            subprocess.Popen: The started process

        Raises:
            OSError: The process cannot be started
        """
        environ = dict(os.environ)
        # The install context is written from the parsed target, not read back
        # from the ambient environment: the values this process judged are the
        # values the updater works on, whatever the environment holds.
        target = self.target
        environ[ENV_ASAR] = target.asar
        environ[ENV_EXE] = target.exe
        environ[ENV_CWD] = target.cwd
        environ[ENV_ARGV] = json.dumps(target.argv)
        environ[ENV_ELECTRON_PID] = str(target.electron_pid)
        # an obsolete variable of an earlier generation: the updater takes the
        # marker from ALASIO_RELAUNCH_TARGETS, nothing reads this one any more
        environ.pop('ALASIO_APP_ELECTRON_START', None)
        environ[ENV_RELAUNCH_TARGETS] = json.dumps(self.targets(), separators=(',', ':'))
        environ[ENV_RELAUNCH_SHA256] = staged.sha256
        environ[ENV_RELAUNCH_TIMEOUT] = str(RELAUNCH_TIMEOUT)
        environ[ENV_RESUME_FILE] = str(resume_file)
        environ[ENV_RESUME_TOKEN] = credential
        kwargs = {
            # the working directory of the installation: the updater writes its
            # log under it, and the application it starts runs there
            'cwd': str(env.PROJECT_ROOT),
            'env': environ,
            'stdin': subprocess.DEVNULL,
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL,
            'close_fds': True,
        }
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs['start_new_session'] = True
        script = self.updater_script()
        process = subprocess.Popen([sys.executable, str(script)], **kwargs)
        logger.info(f'[Shell] Updater started: pid={process.pid} {script}')
        return process


# The process-wide shell update, configured by create_config()
SHELL_UPDATE = ShellUpdate()
