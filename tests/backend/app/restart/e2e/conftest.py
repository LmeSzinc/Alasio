"""
The realcase end-to-end runs of the restart flows: the wiring of one backend

Every collaborator of the restart package is the real one here:

- the manager, the flows and the steps (``alasio.backend.app.restart``): the
  process wide ``RestartManager``, wired by ``Backend`` the way a backend
  process wires it (the worker manager reads its start gates from it);
- the check module (``UpdateCheck`` / ``ModUpdateCheck``): the real states and
  the real check rhythm; the mods are real ``Mod`` objects whose roots live in
  the in-memory filesystem and whose mirrors point at the mock update servers
  below;
- the deploy jobs (``DeployJob`` / ``UnpackJob`` / ``UpdateJob``): the real
  pack flows, served by real packs (the real ``PackFull`` / ``PackUpdate``
  encoder over a ``MockGitRepo``);
- the worker manager (``BackendWorkerManager``): the real state machine --
  its states, its pipe bookkeeping, its recv loop and its disconnect
  handling are the ones of the process -- over in-process worker *peers*
  (``MockWorkerProcess``): the peer speaks the real bridge protocol on a real
  pipe, no OS process is started;
- the resume file IO (``ResumeFiles``) and the daily bookkeeping
  (``DailyState``);
- the deploy config (``MockDeployConfig``): the values of the test instead of
  a yaml file, while the readers around it stay real (``UpdateCheck``
  classifies the mods from ``Update.AutoUpdate``, ``DailyState.target`` parses
  ``Update.AutoRestartTime``).

The filesystem is the in-memory one of the backend tests
(``alasio.testing.filesystem``): every file of the backend, of the project
root and of the mod roots lives in memory. The second seam is the config scan
(``InMemoryConfigScan``): the real scan reads the config databases through
the sqlite C layer, which the in-memory filesystem cannot serve.
Everything the restart steps read from the scan (the config and its mod) is
the data of the real source class.

The supervisor restart is the one action with no real implementation (a test
process is not spawned by a supervisor): ``RestartRequest`` records it, and
the supervisor pipe is the stand-in of ``tests/backend/conftest.py``.

The runs change no timing constant: the resume queue paces itself by the
report of each worker (``WorkerManager.worker_wait_started``), and a scenario
that needs the queue to wait closes the report gate of the peers (see
``MockWorkerProcess.REPORT_GATE`` and the ``report_gate`` fixture), so no
scenario has to race a fixed window.
"""
import datetime
import multiprocessing
import os
import threading
from types import SimpleNamespace

import httpx2
import msgspec
import pytest
import trio
import trio.testing

from alasio.backend.app.restart import restart_step as step_module, resume_file as resume_file_module
from alasio.backend.app.restart.daily import DailyState
from alasio.backend.app.restart.restart_manager import RESTART_MANAGER
from alasio.backend.app.restart.resume_file import ResumeFiles, ResumeRecord
from alasio.backend.app.restart.update_check import UpdateCheck
from alasio.backend.topic import worker as topic_worker_module
from alasio.backend.topic._worker import BackendWorkerManager
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.worker.event import DECODER_CACHE, ConfigEvent
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.config.entry.const import ModEntryInfo
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.mod import Mod
from alasio.config.table.scan import ConfigInfo
from alasio.deploy.config import model as deploy_config_module
from alasio.deploy.pack import job as job_module
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy.pack.server_file import ServerFile
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.testing.filesystem import fs  # noqa: F401
from alasio.testing.patch_time import PatchTime
from alasio.testing.static_file import MockStaticFile
from tests.deploy_dev.pack.conftest import FakeMirrorTable, MemoryLock, make_repo

# =============================================================================
# The packs and the mock update servers (module level: encoded once)
# =============================================================================

# The installed version and the published one of the updates
V1 = 'v1'
V2 = 'v2'

REPO = make_repo({
    V1: {
        'module/main.py': b'print("v1")\n',
        'docs/readme.md': b'# e2e\n\ninstalled\n',
    },
    V2: {
        'module/main.py': b'print("v2")\n',
        'docs/readme.md': b'# e2e\n\ninstalled\n',
        'docs/new.txt': b'added by v2\n',
    },
})
FULL_V1 = b''.join(PackFull(REPO, commit=V1).iter_pack_data())
INDEX_V1 = b''.join(PackFull(REPO, commit=V1).iter_packidx_data())
FULL_V2 = b''.join(PackFull(REPO, commit=V2).iter_pack_data())
INDEX_V2 = b''.join(PackFull(REPO, commit=V2).iter_packidx_data())
UPDATE_V1_V2 = b''.join(PackUpdate(PackFull(REPO, commit=V2), V1).iter_pack_data())

# The hosts of the mirror entries: the dispatcher of MockUpdateBackend routes
# the requests of the shared client by them
UPDATE_HOST = 'update.mock'
# a mirror the test can break between the check and the apply
DYING_HOST = 'dying.mock'
BROKEN_HOST = 'broken.mock'
# a mirror whose files are the output of a real pack generator, registered
# under the names the generator wrote
GENERATED_HOST = 'generated.mock'

# The one-shot credential token of the resume files (secrets.token_hex is
# patched to it: the real token is random by design)
TOKEN = '5c' * 16

# The default target of the daily maintenance (config/deploy.yaml)
RESTART_TIME = '03:50'
# The wall clock of the runs: before the daily target
NOW = (2, 0)


class MockUpdateServer:
    """
    One update server of the runs: the packs of the published versions, served
    in memory by a static file server (the range requests and the 404 of an
    unknown path of a deployment)

    The layout is the one of a deployment: latest.pack (the version and the
    checksum of its index pack), {version}/full.pack and
    {new_version}/update_{old_version}.pack. Nothing registered means every
    request is a 404: a mirror that is down.
    """

    def __init__(self, url):
        self.url = url
        self.static = MockStaticFile()
        self.latest = ''

    def register_version(self, version, full, index):
        """Publish a version; it becomes the latest one"""
        self.latest = version
        self.static.register_file(f'{version}/full.pack', full)
        checksum = bytes.fromhex(PackDecodeBase(index).index_checksum)
        self.static.register_file('latest.pack', version.encode() + checksum)

    def register_update(self, old, new, data):
        """Publish the update pack of a version pair"""
        self.static.register_file(f'{new}/update_{old}.pack', data)

    def register_folder(self, prefix, folder):
        """
        Publish every file below a folder at its relative path.

        The names on the disk are kept: a deployment serves exactly the files
        a generator wrote, so a client that reads another name gets a 404
        instead of a test that maps the names by hand.

        Args:
            prefix (str): Path prefix of the channel below the host
            folder (PathStr): Folder to publish
        """
        folder = PathStr.new(folder)
        root = str(folder).rstrip('/\\')
        for path in folder.iter_files(recursive=True):
            relative = str(path)[len(root):].replace('\\', '/').lstrip('/')
            self.static.register_file(f'{prefix}/{relative}', path.atomic_read_bytes())


class MockUpdateBackend:
    """
    The update backend of a run: the mock update servers of the mirror entries

    The instance is the ASGI app of the update traffic of the runs: the host
    of a request dispatches it to the server of the mirror entry, so every
    request of every mod channel goes through one client, the way one backend
    reaches several mod channels.

    ``hold()`` parks the requests whose path contains a text until the test
    opens the gate: the slow mirror of the cancellation tests.
    ``break_host()`` takes a mirror down from a point on (every request is a
    404).

    Attributes:
        servers (dict[str, MockUpdateServer]): The server of every host
    """

    def __init__(self):
        self.servers = {
            host: MockUpdateServer(f'http://{host}')
            for host in (UPDATE_HOST, DYING_HOST, BROKEN_HOST, GENERATED_HOST)
        }
        # every publishing mirror starts on the installed version V1
        for host in (UPDATE_HOST, DYING_HOST):
            self.servers[host].register_version(V1, FULL_V1, INDEX_V1)
        # the broken mirror (BROKEN_HOST) publishes nothing: every request is
        # a 404
        self.broken = set()
        self.hold_match = ''
        self.hold_gate = None
        self.hold_entered = trio.Event()

    def publish_update(self):
        """
        Publish V2 and its update pack on the publishing mirrors (a new
        version while the backend runs)
        """
        for host in (UPDATE_HOST, DYING_HOST):
            server = self.servers[host]
            server.register_version(V2, FULL_V2, INDEX_V2)
            server.register_update(V1, V2, UPDATE_V1_V2)

    def break_host(self, host):
        """
        Take a mirror down: every request to it answers 404 from now on (a
        mirror that died while the backend ran)
        """
        self.broken.add(host)

    def hold(self, match, gate):
        """
        Park the requests whose path contains ``match`` until ``gate`` is set

        ``hold_entered`` is set when the first held request arrives: the test
        knows the flow is waiting on the mirror. The gates belong to the trio
        run of the test.

        Args:
            match (str): Path fragment, e.g. '/latest.pack' or 'update_v1.pack'
            gate (trio.Event): The gate the test opens
        """
        self.hold_match = match
        self.hold_gate = gate
        self.hold_entered = trio.Event()

    async def __call__(self, scope, receive, send):
        host = dict(scope.get('headers') or ()).get(b'host', b'').decode()
        server = self.servers.get(host, None)
        if server is None:
            raise AssertionError(f'No mock update server behind host "{host}"')
        if host in self.broken:
            await send({'type': 'http.response.start', 'status': 404, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})
            return
        if self.hold_gate is not None and self.hold_match in scope['path']:
            self.hold_entered.set()
            await self.hold_gate.wait()
        await server.static(scope, receive, send)


class ChildConn:
    """
    The child end of a worker pipe as the spawn tail of the manager sees it

    The real spawn tail closes its copy of the child end right after the
    process started (the worker owns the other copy); the worker peer of the
    runs lives in this process and keeps using the same connection, so the
    close of the copy is a no-op here (the peer closes the connection itself
    when it exits).
    """

    def __init__(self, conn):
        self.conn = conn

    def close(self):
        pass


class MockWorkerProcess:
    """
    The worker "process" of the runs: an in-process peer on the worker side of
    the pipe, no OS process is started

    The peer speaks the real wire protocol of the worker bridge
    (``alasio.backend.worker.event``): it reports the worker "running" once
    and it exits -- closing its end of the pipe, the manager's own recv loop
    then finalizes the entry through its real disconnect path -- when the
    manager sends the scheduler-stop command. A worker mod that ignores the
    graceful stop (``WorkerTestInfinite``) keeps running instead and only goes
    down through the kill command of the timeout escalation, like its real
    counterpart.

    The interface is the slice of ``multiprocessing.Process`` the worker
    manager uses: start / is_alive / join / terminate / pid / exitcode.
    """
    # worker mods whose scheduler ignores the graceful stop (see
    # tests/backend/worker/worker_mods.py)
    IGNORES_GRACEFUL_STOP = ('WorkerTestInfinite',)
    # The gate of the first report of every peer (open by default, see the
    # report_gate fixture). A closed gate parks the report of the worker, so a
    # scenario that needs the resume queue to wait for a worker that has not
    # reported itself holds that moment from the test instead of racing a fixed
    # window. The peer keeps serving the pipe while the report is parked, like
    # a real worker that is still booting when the manager sends it a stop
    # command
    REPORT_GATE = threading.Event()
    # how long the peer takes to go down after the stop command: a real worker
    # needs about half a second to stop, the peer only a moment -- long enough
    # that the manager's own logs of the wait land first (its log lines are
    # written before it starts polling), like they do with a real worker. A
    # real wait, not time.sleep: the runs freeze time.sleep
    STOP_DELAY = 0.05

    def __init__(self, mod, config, conn, name=''):
        self.mod = mod
        self.config = config
        self.name = name
        self.conn = conn
        self.pid = 0
        self.exitcode = None
        self._thread = None

    def start(self):
        """Start the peer threads (the "worker" reports running through the pipe)"""
        self.pid = os.getpid()
        if self.REPORT_GATE.is_set():
            # the plain case: the report goes out before the peer serves the
            # pipe, like a real worker announces itself while it boots
            self._report_running()
        else:
            # a held report must not park the spawn itself: the queue would
            # never see the process of the worker it waits for. Its own thread
            # parks on the gate while the peer keeps serving the pipe
            report = threading.Thread(target=self._report_running, name=f'{self.name}-report', daemon=True)
            report.start()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def _report_running(self):
        """Report the state of the worker through the pipe (see REPORT_GATE)"""
        try:
            self.REPORT_GATE.wait()
            self.conn.send_bytes(msgspec.msgpack.encode(ConfigEvent(t='WorkerState', v='running')))
        except OSError:
            # the manager closed the pipe while the report was parked: the
            # entry was finalized without it
            pass

    def _run(self):
        """Play the worker protocol until the manager stops the worker"""
        try:
            stopped_by_command = False
            while True:
                try:
                    data = self.conn.recv_bytes()
                except (EOFError, OSError):
                    break
                command = DECODER_CACHE.COMMAND_EVENT.decode(data)
                if command.c not in ('scheduler-stopping', 'kill', 'force-kill'):
                    continue
                if command.c == 'scheduler-stopping' and self.mod in self.IGNORES_GRACEFUL_STOP:
                    continue
                stopped_by_command = True
                break
            # the graceful stop of a worker takes a moment in production (it
            # finishes its current task first): the short wait keeps the order
            # of the manager's logs the one of a real run. A pipe the manager
            # closed underneath the peer means a force kill, and a killed
            # process is gone at once, so the wait is skipped
            if stopped_by_command:
                threading.Event().wait(self.STOP_DELAY)
        finally:
            self.exitcode = 0
            try:
                self.conn.close()
            except OSError:
                pass

    def is_alive(self):
        return bool(self._thread is not None and self._thread.is_alive())

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def terminate(self):
        try:
            self.conn.close()
        except OSError:
            pass
        self.join(1.0)
        if self.exitcode is None:
            self.exitcode = -15

    def process_graceful_kill(self):
        self.terminate()


class MockProcessContext:
    """
    The process factory of the runs: ``Process()`` / ``Pipe()`` of a spawn
    context, the "process" is MockWorkerProcess

    The worker manager is the real class: it drives the real state machine
    (starting / running / scheduler-stopping / restarting / resuming), the
    real pipe bookkeeping and its real recv loop and disconnect handling; only
    the OS process behind an entry is an in-process peer.
    """

    def Process(self, target=None, args=(), name='', daemon=True):
        # the manager calls this with the test-mod shape (mod, config, conn) and
        # with the real-mod shape (mod, config, conn, project_root, mod_root,
        # path_main): the peer only needs the first three
        mod, config, child_conn = args[0], args[1], args[2]
        conn = child_conn.conn if isinstance(child_conn, ChildConn) else child_conn
        return MockWorkerProcess(mod, config, conn, name=name)

    @staticmethod
    def Pipe(duplex=True):
        parent_conn, child_conn = multiprocessing.Pipe(duplex=duplex)
        return parent_conn, ChildConn(child_conn)


class InMemoryConfigScan(ConfigScanSource):
    """
    The config scan of the runs: the configs of the test, without the config
    databases

    The real scan reads the config folder of the project through the sqlite C
    layer, which the in-memory filesystem cannot serve. Everything the restart
    steps read from the scan (the configs and the mod of each one) is here,
    with the data model of the real source itself.
    """
    CONFIGS = {}

    def on_init(self):
        return dict(self.CONFIGS)


class RestartRequest:
    """The supervisor restart of the handover: recorded, may fail"""

    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    async def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error


class MockDeployConfig:
    """
    The deploy config of the runs: the values of the test, without any file

    The real ``DeployConfig`` reads (and rewrites) ``config/deploy.yaml``
    through the yaml config layer; nothing of the deploy config itself is
    under test here, so the two readers of the process -- the update switch of
    the check module and the restart time of the daily bookkeeping -- ask this
    stand-in. The restart-module code around them stays real:
    ``UpdateCheck.load_schedule`` reads ``Update.AutoUpdate`` from it and
    ``DailyState.target`` parses ``Update.AutoRestartTime``.

    Attributes:
        auto_update (bool): Deploy.Update.AutoUpdate of the run
        restart_time (str): Deploy.Update.AutoRestartTime of the run
    """
    auto_update = True
    restart_time = RESTART_TIME

    def __init__(self):
        self.config = self

    @property
    def data(self):
        return SimpleNamespace(Update=SimpleNamespace(
            AutoUpdate=self.auto_update, AutoRestartTime=self.restart_time))


class Project:
    """
    The project root of the runs, in the in-memory filesystem

    Attributes:
        root (PathStr): The project root (``env.PROJECT_ROOT``)
        files (ResumeFiles): The real resume file IO of the process
    """

    def __init__(self, fs, monkeypatch):
        self.fs = fs
        self.root = PathStr.new(fs.root_dir.path)
        monkeypatch.setattr(env, 'PROJECT_ROOT', self.root)
        # the job lock is a SQLite file lock: sqlite opens its file in the C
        # layer, which the in-memory filesystem cannot intercept (the same
        # in-memory lock the pack tests use)
        monkeypatch.setattr(job_module, 'SQLiteFileLock', MemoryLock)
        # the mirror selection record: an in-memory table of the real schema
        monkeypatch.setattr(ServerFile, 'RECORD_TABLE', FakeMirrorTable())
        self.files = ResumeFiles(folder=self.root.joinpath('log/resume'))

    def mod_root(self, mod_name):
        """
        Returns:
            PathStr: The root of a mod of a run
        """
        return self.root.joinpath(f'mods/{mod_name}')

    async def install(self, mod_name, pack=FULL_V1):
        """
        Install a pack into the root of a mod (the local ledger of the run)
        """
        await UnpackJob(pack, root=self.mod_root(mod_name)).run()


class Backend:
    """
    One backend process of a scenario: the real wiring and its state

    Created by the ``make_backend`` fixture (a test creates as many as it
    needs); every collaborator is the real one, see the module docstring.

    Attributes:
        manager (RestartManager): The process wide restart manager (the object
            the real worker manager reads its start gates from)
        mods (dict[str, Mod]): The mods of the process, keyed by mod name
        update_check (UpdateCheck): The real check module of the process
        worker_manager (BackendWorkerManager): The real worker manager (fresh
            state: one per backend process)
        daily (DailyState): The real daily bookkeeping (reads the fake clock)
        files (ResumeFiles): The real resume file IO of the project
        restart (RestartRequest): The supervisor restart of the handover
        servers (MockUpdateBackend): The mock update servers of the mirrors
    """

    def __init__(self, project, servers, mods=None, configs=None, restart_error=None, monkeypatch=None,
                 pack_deps=None):
        """
        Args:
            project (Project): The project root of the run
            servers (MockUpdateBackend): The mock update backend of the run
            mods (dict[str, str]): {mod name: mirror url}; Defaults to one mod
                of the update server, run by the worker test mod
                'WorkerTestScheduler'
            configs (dict[str, str]): {config name: mod name}; Defaults to
                'cfg_a' on the default mod
            pack_deps (dict[str, Iterable[str]]): {mod name: the names the
                entry declares for pack updates}, see
                ModEntryInfo.update_pack_deps. Defaults to None, every mod
                declares none
            monkeypatch: The pytest monkeypatch fixture (the manager spawn is
                watched through it)
        """
        self.project = project
        self.servers = servers
        if mods is None:
            mods = {'WorkerTestScheduler': f'http://{UPDATE_HOST}'}
        pack_deps = pack_deps or {}
        self.mods = {
            name: Mod(ModEntryInfo(
                name=name, root=str(project.mod_root(name)), mirrors=mirrors,
                update_pack_deps=tuple(pack_deps.get(name, ()))))
            for name, mirrors in mods.items()
        }
        if configs is None:
            configs = {'cfg_a': next(iter(self.mods))}
        self.configs = dict(configs)
        # the mod lookup of the process (the real get_mod reads it)
        MOD_LOADER.dict_mod = dict(self.mods)
        InMemoryConfigScan.CONFIGS = {
            name: ConfigInfo(name=name, mod=mod, gid=index + 1, iid=1)
            for index, (name, mod) in enumerate(self.configs.items())
        }
        # the client of every request of the check module and of the jobs: a
        # real httpx2 client whose transport is the ASGI app of the servers
        self.client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=servers))
        self.update_check = UpdateCheck()
        self.update_check.bind(mods=self.mods)
        self.update_check._client = self.client
        # one worker manager per backend process: the real state machine over
        # the in-process worker peers of the runs (no OS process is started)
        BackendWorkerManager.singleton_clear()
        self.worker_manager = BackendWorkerManager()
        self.worker_manager._ctx = MockProcessContext()
        self.daily = DailyState()
        self.files = project.files
        self.restart = RestartRequest(restart_error)
        self.manager = RESTART_MANAGER
        self.manager.reset()
        self.manager._worker_manager = self.worker_manager
        self.manager.update_check = self.update_check
        self.manager.daily = self.daily
        self.manager.files = self.files
        self.manager.restart_fn = self.restart
        # the worker state hooks of BackendWorkerManager publish through the
        # global context of the backend
        GLOBAL_CONTEXT.trio_token = trio.lowlevel.current_trio_token()
        # watch the tasks the manager spawns (the view pushes and the resume
        # queue spawned when a flow releases the slot): wait_quiet waits for
        # them, the complete-log assertions are taken when nothing of the run
        # is in flight any more
        self._tasks = 0
        original_spawn = self.manager.spawn

        def watched_spawn(async_fn, *args):
            async def watched():
                try:
                    await async_fn(*args)
                finally:
                    self._tasks -= 1

            self._tasks += 1
            try:
                original_spawn(watched)
            except BaseException:
                self._tasks -= 1
                raise

        if monkeypatch is not None:
            monkeypatch.setattr(self.manager, 'spawn', watched_spawn)

    async def install(self, mod_name, pack=FULL_V1):
        """
        Install a pack into the root of a mod (the local ledger of the run,
        what a previous process left on disk)
        """
        await self.project.install(mod_name, pack)

    @property
    def resume_file(self):
        """
        Returns:
            PathStr: The path of the resume file of this process (the fixed
                token of the runs)
        """
        return self.files.file_of(TOKEN)

    # ------------------------------------------------------------------
    # The workers (real subprocesses)
    # ------------------------------------------------------------------

    async def start_worker(self, config):
        """
        Start a worker like the Worker topic rpc does and wait until it runs
        """
        mod = self.mods[self.configs[config]]
        success, msg = await trio.to_thread.run_sync(self.worker_manager.worker_start, mod, config)
        assert success, msg
        await self.wait_running(config)

    async def wait_running(self, config, timeout=20.0):
        """Wait until the worker of a config reported running"""
        running = await trio.to_thread.run_sync(self.worker_manager.worker_wait_running, config, timeout)
        assert running, f'The worker of "{config}" did not start'

    def close(self):
        """Kill the workers of the run (the backend process is going away)"""
        try:
            self.worker_manager.close()
        except Exception as e:  # pragma: no cover - teardown only
            print(f'Failed to close the worker manager: {e}')

    # ------------------------------------------------------------------
    # The manager run
    # ------------------------------------------------------------------

    async def start(self, nursery):
        """
        Run the lifespan tasks of the backend (the check module and the manager)
        and wait for the backend to be idle: the startup flow and its resume
        queue ended
        """
        nursery.start_soon(self.update_check.run)
        nursery.start_soon(self.manager.run)
        await wait_until(lambda: self.manager.nursery is not None)
        await self.wait_quiet()

    async def wait_quiet(self, timeout=30.0):
        """
        Wait until the flow slot is free and every task the manager spawned
        ended (the point the complete-log assertions are taken at)
        """
        await wait_until(lambda: not self.manager.flow_running and not self._tasks, timeout=timeout)
        await trio.testing.wait_all_tasks_blocked()


def rendered(capture):
    """
    The complete captured log, one line per event, for the exact assertions

    ``<level> | <message>`` like the log file writes it, plus the raw logs as
    they are. The traceback of a ``logger.exception`` is rendered as the single
    line ``<traceback>``: its text carries the file paths and line numbers of
    the run, the message before it is asserted as printed.

    Args:
        capture (CaptureWriter): The writer of logger.mock_capture_writer()

    Returns:
        str: The log text, newline terminated
    """
    lines = []
    for event in capture.backend.logs:
        lines.append(event['m'] if event.get('r') else f"{event['l']} | {event['m']}")
        if event.get('e'):
            lines.append('<traceback>')
    return ''.join(f'{line}\n' for line in lines)


def assert_logs(capture, expected):
    """
    Assert the complete log of the run (the specification of the test)

    On a mismatch the complete actual text is printed before the assertion, so
    the reader sees what the run printed instead of a truncated diff.

    Args:
        capture (CaptureWriter): The writer of logger.mock_capture_writer()
        expected (str): The complete expected text, see rendered()
    """
    actual = rendered(capture)
    if actual != expected:
        print(f'\n==== actual log of the run ====\n{actual}==== end of the actual log ====\n', flush=True)
    assert actual == expected


async def wait_until(predicate, timeout=20.0):
    """
    Wait for a condition of the run (the tests drive the scenarios with it)
    """
    with trio.fail_after(timeout):
        while not predicate():
            await trio.sleep(0.01)


async def wait_log(capture, text, timeout=20.0):
    """Wait until a log line of the scenario was printed"""
    await wait_until(lambda: capture.fd.any_contains(text), timeout=timeout)


def at(hour, minute=0, day=13, second=0):
    """
    The epoch second whose local wall clock is 2026-10-<day> <hour>:<minute>

    The wall clock of the runs is the local one (the daily maintenance follows
    the local small hours) and a naive datetime is read as local by
    ``timestamp()``: an instant built here is the local wall clock the tests
    mean, whatever the time zone of the host.
    """
    return datetime.datetime(2026, 10, day, hour, minute, second).timestamp()


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def report_gate():
    """
    Open the report gate of the peers (see MockWorkerProcess.REPORT_GATE)

    A test that wants the resume queue to wait for a worker closes the gate
    before the backend runs: the report of that worker then arrives when the
    test wants it, so no scenario needs a timing window to hit its race.
    """
    MockWorkerProcess.REPORT_GATE.set()
    yield
    MockWorkerProcess.REPORT_GATE.set()


@pytest.fixture(autouse=True)
def clock():
    """
    The wall clock of the runs: 2026-10-13 02:00 local

    Before the daily target (03:50), so a crossing is what a test moves the
    clock over; the epoch of the fake clock is what the logs print as
    ``updated_all_at``.
    """
    with PatchTime(at(*NOW)) as patch:
        yield patch


@pytest.fixture(autouse=True)
def scan(monkeypatch):
    """The config scan of the runs, in place of the sqlite backed real one"""
    InMemoryConfigScan.singleton_clear()
    InMemoryConfigScan.CONFIGS = {}
    monkeypatch.setattr(step_module, 'ConfigScanSource', InMemoryConfigScan)
    monkeypatch.setattr(topic_worker_module, 'ConfigScanSource', InMemoryConfigScan)
    yield
    InMemoryConfigScan.singleton_clear()


@pytest.fixture(autouse=True)
def project(fs, monkeypatch):
    """The project root of the runs (the in-memory filesystem)"""
    return Project(fs, monkeypatch)


@pytest.fixture(autouse=True)
def deploy_config(monkeypatch):
    """
    The deploy config of the runs: mock values instead of ``config/deploy.yaml``

    A test that needs the mod updates off sets ``deploy_config.auto_update``
    before the backend is created (the check module reads the switch at
    startup).
    """
    MockDeployConfig.auto_update = True
    MockDeployConfig.restart_time = RESTART_TIME
    monkeypatch.setattr(deploy_config_module, 'DeployConfig', MockDeployConfig)
    return MockDeployConfig


@pytest.fixture(autouse=True)
def servers():
    """The mock update servers of the mirror entries"""
    return MockUpdateBackend()


@pytest.fixture
def make_backend(project, servers, monkeypatch):
    """
    Factory of the backends of one test

    Every backend created by a test is torn down with it: the worker
    subprocesses are killed and the process wide manager is reset (a fresh
    backend process in production).
    """
    created = []

    def factory(mods=None, configs=None, restart_error=None, pack_deps=None):
        backend = Backend(project, servers, mods=mods, configs=configs,
                          restart_error=restart_error, monkeypatch=monkeypatch,
                          pack_deps=pack_deps)
        created.append(backend)
        return backend

    yield factory
    for backend in created:
        backend.close()
    RESTART_MANAGER.reset()
    # the worker state hook of BackendWorkerManager publishes through the
    # global context: the token of a finished run belongs to no run
    GLOBAL_CONTEXT.trio_token = None
    # the mod lookup is a cached property of the process wide loader: drop the
    # test value so the next reader builds the real one
    cached_property.pop(MOD_LOADER, 'dict_mod')


@pytest.fixture(autouse=True)
def resume_token(monkeypatch):
    """
    The one-shot credential of the resume file: the fixed token of the runs

    The token of a publication is random by design (the isolation of a
    transaction), the tests pin it to assert the log and the file path
    exactly.
    """
    monkeypatch.setattr(resume_file_module.secrets, 'token_hex', lambda size: TOKEN)


def read_record(backend):
    """
    Returns:
        ResumeRecord: The record published by the process of the run
    """
    return msgspec.json.decode(atomic_read_bytes(backend.resume_file), type=ResumeRecord)


async def write_resume(backend, configs, actions=None, updated_all_at=0.0):
    """
    Publish a resume file like the previous process did (the real writer)

    Returns:
        str: The credential the supervisor injects into the new process
    """
    return await backend.files.write(configs, actions=actions, updated_all_at=updated_all_at)


def credential_of(backend):
    """
    Returns:
        str: The credential the supervisor would inject for the file the run
            published
    """
    payload = atomic_read_bytes(backend.resume_file)
    return f'{TOKEN}-{backend.files.checksum(TOKEN, payload)}'
