"""
Tests for the graceful backend restart module (alasio/backend/restart.py)

Covers the resume file protocol (write / read / credential verification /
cleanup), the orchestration of the old backend (run_graceful_restart) and the
consume + auto-resume of the new backend (resume_after_restart).

The resume file lives in the in-memory filesystem (fs fixture): every test
points env.PROJECT_ROOT at the fake root.
"""
import builtins
import json
import multiprocessing
import threading
import time

import msgspec
import pytest
import trio

from alasio.backend import restart
from alasio.backend.restart import (
    GRACEFUL_RESTART, RESUME_TOKEN_ENV, ResumeRecord, cancel_graceful_restart, resume_after_restart,
    run_graceful_restart
)
from alasio.backend.topic.restart import RestartSource
from alasio.backend.worker.manager import WorkerManager
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.logger.writer import LogWriter
from alasio.testing.filesystem import FakeFilesystem
from tests.backend.worker.const import *
from tests.backend.worker.test_worker_lifespan import assert_recv_thread_gone, assert_worker_gone


class FakeScan:
    """
    Stand-in for ConfigScanSource in tests: fixed data, no disk access
    """

    def __init__(self, data):
        self.data = data

    async def reinit(self, force=False):
        return None


class SpawnSafeManager(WorkerManager):
    """
    WorkerManager that lifts the fake filesystem while spawning a worker

    multiprocessing spawn needs the real os.* in the parent (the child
    preparation data is written into a pipe fd with builtins.open), which the
    fake filesystem patches; without lifting it the worker child never receives
    its startup data. Only the spawn window is lifted -- the file operations of
    the test itself stay on the fake filesystem.
    """

    def __init__(self, fake_fs):
        super().__init__()
        self._fs = fake_fs

    def worker_start(self, *args, **kwargs):
        return self._spawn_safe(super().worker_start, *args, **kwargs)

    def worker_resume(self, *args, **kwargs):
        return self._spawn_safe(super().worker_resume, *args, **kwargs)

    def _spawn_safe(self, func, *args, **kwargs):
        # the fake filesystem is lifted for the spawn (multiprocessing needs the
        # real os.*): re-mute the log file target, or a log line inside this
        # window would open a real log file under the fake PROJECT_ROOT
        LogWriter().close_fd()
        LogWriter().mute(fd=True)
        self._fs.deactivate()
        try:
            return func(*args, **kwargs)
        finally:
            self._fs.activate()
            LogWriter().close_fd()


@pytest.fixture
def fake_fs():
    """
    An explicitly activated fake filesystem (togglable)

    The shared `fs` fixture patches through pytest's monkeypatch and has no
    working deactivate() path, while the spawn-safe manager must lift the
    patches around process spawns.
    """
    fake = FakeFilesystem()
    fake.activate()
    LogWriter().close_fd()
    yield fake
    LogWriter().close_fd()
    fake.deactivate()


@pytest.fixture
def project_root(fake_fs):
    """Point env.PROJECT_ROOT at a folder of the fake filesystem root"""
    old = env.PROJECT_ROOT
    root = PathStr.new(fake_fs.root_dir.path).joinpath('project')
    root.makedirs()
    env.PROJECT_ROOT = root
    yield root
    env.PROJECT_ROOT = old


@pytest.fixture
def manager(fake_fs):
    """Create a fresh spawn-safe WorkerManager instance"""
    WorkerManager.singleton_clear()
    mgr = SpawnSafeManager(fake_fs)
    yield mgr
    try:
        mgr.close()
    except Exception as e:
        print(f"Warning: Error during cleanup: {e}")


@pytest.fixture(autouse=True)
def restart_state():
    """Reset the restart module state and the Restart topic around each test"""
    RestartSource.singleton_clear()
    GRACEFUL_RESTART.reset()
    yield
    RestartSource.singleton_clear()
    GRACEFUL_RESTART.reset()


def start_worker(manager, mod, config, timeout=WORKER_STARTUP_TIMEOUT):
    """
    Start a real worker subprocess and wait until it is running

    Args:
        manager (WorkerManager): Manager to start on
        mod (str): Test mod name
        config (str): Config name
        timeout (float): Startup timeout

    Returns:
        WorkerState: The running worker entry
    """
    success, msg = manager.worker_start(mod, config)
    assert success, f'Failed to start worker: {msg}'
    state = manager.state[config]
    assert state.wait_running(timeout=timeout), f'Worker did not start: {config}'
    return state


def read_record(file):
    """
    Decode a written resume file

    Args:
        file (PathStr): Resume file path

    Returns:
        ResumeRecord: Decoded record
    """
    return msgspec.json.decode(file.atomic_read_bytes(), type=ResumeRecord)


# ============================================================================
# Resume file protocol
# ============================================================================

class TestResumeFileIO:
    """write_resume / read_resume and the token-checksum credential"""

    @pytest.mark.trio
    async def test_write_read_roundtrip(self, project_root, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a', 'cfg_b'])
        token, sep, checksum = credential.partition('-')
        assert sep and token and checksum

        file = GRACEFUL_RESTART.resume_file_of(token)
        assert file.isfile()
        payload = file.atomic_read_bytes()
        # the credential's checksum is the HMAC of the exact file content
        assert GRACEFUL_RESTART.resume_checksum(token, payload) == checksum
        record = json.loads(payload)
        assert record['owner'] == 'restart'
        assert record['configs'] == ['cfg_a', 'cfg_b']
        assert record['actions'] == []
        assert record['ts'] > 0

        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        read = GRACEFUL_RESTART.read_resume()
        assert isinstance(read, ResumeRecord)
        assert read.configs == ['cfg_a', 'cfg_b']
        assert read.owner == 'restart'
        # read once: the file is deleted by the read
        assert not file.isfile()

    @pytest.mark.trio
    async def test_write_with_actions_and_owner(self, project_root):
        await GRACEFUL_RESTART.write_resume([], owner='update', actions=['test_action'])
        files = GRACEFUL_RESTART.iter_resume_files()
        assert len(files) == 1
        record = read_record(files[0])
        assert record.configs == []
        assert record.actions == ['test_action']
        assert record.owner == 'update'

    @pytest.mark.trio
    async def test_write_keeps_other_files(self, project_root):
        # another backend (or a dead session) may share the folder: a write
        # must never delete a foreign resume file. Only the credential holder
        # can consume a file, so a leftover is harmless
        foreign = GRACEFUL_RESTART.resume_file_of('0123456789abcdef')
        foreign.atomic_write(b'{"foreign": true}')

        await GRACEFUL_RESTART.write_resume(['cfg_a'])

        assert foreign.isfile()
        assert len(GRACEFUL_RESTART.iter_resume_files()) == 2

    @pytest.mark.trio
    async def test_read_only_reads_own_token_file(self, project_root, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a'])
        # a foreign file (another token) must be neither read nor deleted
        foreign = GRACEFUL_RESTART.resume_file_of('ffffffffffffffff')
        foreign.atomic_write(b'{"foreign": true}')

        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        read = GRACEFUL_RESTART.read_resume()

        assert read is not None
        assert foreign.isfile()

    @pytest.mark.trio
    async def test_read_without_credential_touches_nothing(self, project_root, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a'])
        file = GRACEFUL_RESTART.resume_file_of(credential.partition('-')[0])

        monkeypatch.delenv(RESUME_TOKEN_ENV, raising=False)
        assert GRACEFUL_RESTART.read_resume() is None
        # the file is not even opened
        assert file.isfile()

    def test_read_missing_file_returns_none(self, project_root, monkeypatch):
        monkeypatch.setenv(RESUME_TOKEN_ENV, '00ff00ff-checksum')
        assert GRACEFUL_RESTART.read_resume() is None

    def test_read_malformed_credential_returns_none(self, project_root, monkeypatch):
        file = GRACEFUL_RESTART.resume_file_of('deadbeef')
        file.atomic_write(b'{}')

        monkeypatch.setenv(RESUME_TOKEN_ENV, 'no-separator-here-but-token-is-long')
        # 'no' is not a known token: nothing read, nothing deleted
        assert GRACEFUL_RESTART.read_resume() is None
        assert file.isfile()

    @pytest.mark.trio
    async def test_read_tampered_payload_rejected_and_deleted(self, project_root, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a'])
        token = credential.partition('-')[0]
        file = GRACEFUL_RESTART.resume_file_of(token)
        payload = bytearray(file.atomic_read_bytes())
        # flip one byte: the checksum must catch it
        payload[-2] = ord(' ') if payload[-2] != ord(' ') else ord('x')
        file.atomic_write(bytes(payload))

        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        assert GRACEFUL_RESTART.read_resume() is None
        # a read file never survives, valid or not
        assert not file.isfile()

    def test_read_invalid_payload_rejected(self, project_root, monkeypatch):
        # a payload whose checksum is valid but whose schema is not
        token = 'aabbccdd00112233'
        payload = b'{"unknown": 1}'
        checksum = GRACEFUL_RESTART.resume_checksum(token, payload)
        GRACEFUL_RESTART.resume_file_of(token).atomic_write(payload)

        monkeypatch.setenv(RESUME_TOKEN_ENV, f'{token}-{checksum}')
        assert GRACEFUL_RESTART.read_resume() is None
        assert not GRACEFUL_RESTART.resume_file_of(token).isfile()

    @pytest.mark.trio
    async def test_read_rejects_tampered_with_own_token(self, project_root, monkeypatch):
        """The token alone is not enough: the checksum comes from the credential"""
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a'])
        token = credential.partition('-')[0]
        # rewrite the file with a different payload under the same token
        GRACEFUL_RESTART.resume_file_of(token).atomic_write(
            b'{"configs": ["evil"], "owner": "restart", "ts": 0, "actions": []}')

        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        assert GRACEFUL_RESTART.read_resume() is None
        assert not GRACEFUL_RESTART.resume_file_of(token).isfile()


class TestResumeCleanup:
    """resume_cleanup removes only stale resume files"""

    def test_removes_only_stale_resume_files(self, project_root, monkeypatch):
        import os

        monkeypatch.setattr(restart, 'RESUME_CLEANUP_AGE', 100.0)
        fresh = GRACEFUL_RESTART.resume_file_of('aaaa1111')
        stale = GRACEFUL_RESTART.resume_file_of('bbbb2222')
        other = GRACEFUL_RESTART.resume_folder.joinpath('notes.json')
        for file in (fresh, stale, other):
            file.atomic_write(b'{}')
        old = time.time() - 1000
        os.utime(stale, (old, old))

        with logger.mock_capture_writer() as capture:
            GRACEFUL_RESTART.resume_cleanup()
            assert capture.fd.any_contains('[Restart] Resume cleanup: removed 1 stale files, kept 1')

        assert fresh.isfile()
        assert not stale.isfile()
        # unrelated files are not touched
        assert other.isfile()

    def test_missing_folder_is_noop(self, project_root):
        assert not GRACEFUL_RESTART.resume_folder.isdir()
        # must not raise, must not create anything
        GRACEFUL_RESTART.resume_cleanup()
        assert not GRACEFUL_RESTART.resume_folder.isdir()

    def test_empty_folder_is_noop(self, project_root):
        GRACEFUL_RESTART.resume_folder.makedirs()
        GRACEFUL_RESTART.resume_cleanup()
        assert GRACEFUL_RESTART.resume_folder.isdir()


class TestAnnounceResumeToken:
    """announce_resume_token sends the credential over the supervisor pipe"""

    def test_sends_command_resume(self, monkeypatch):
        parent_conn, child_conn = multiprocessing.Pipe()
        monkeypatch.setattr(builtins, '__mpipe_conn__', child_conn, raising=False)
        try:
            GRACEFUL_RESTART.announce_resume_token('token123-checksum456')

            assert parent_conn.poll(timeout=1)
            assert parent_conn.recv_bytes() == b'command:resume:token123-checksum456'
        finally:
            parent_conn.close()
            child_conn.close()

    def test_no_supervisor_is_noop(self, monkeypatch):
        monkeypatch.delattr(builtins, '__mpipe_conn__', raising=False)
        GRACEFUL_RESTART.announce_resume_token('token123-checksum456')

    def test_empty_credential_retracts(self, monkeypatch):
        """An empty credential is the retraction; the supervisor slot is latest-wins"""
        parent_conn, child_conn = multiprocessing.Pipe()
        monkeypatch.setattr(builtins, '__mpipe_conn__', child_conn, raising=False)
        try:
            GRACEFUL_RESTART.announce_resume_token('')

            assert parent_conn.poll(timeout=1)
            assert parent_conn.recv_bytes() == b'command:resume:'
        finally:
            parent_conn.close()
            child_conn.close()


# ============================================================================
# Orchestration (old backend)
# ============================================================================

class TestRunGracefulRestart:
    """run_graceful_restart: wait, write the resume file once, restart"""

    @pytest.mark.trio
    async def test_stops_workers_and_writes_resume(self, project_root, manager, monkeypatch):
        for config in ('cfg_a', 'cfg_b'):
            start_worker(manager, 'WorkerTestScheduler', config)

        calls = []

        async def fake_lifespan_restart():
            calls.append('restart')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        credentials = []
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', credentials.append)

        with logger.mock_capture_writer() as capture:
            await run_graceful_restart(manager)
            # the log names every config the wait covers
            assert capture.fd.any_contains(
                "waiting up to 600s for the workers to stop, waiting for: ['cfg_a', 'cfg_b']")
            # and the final resume list handed to the backend restart
            assert capture.fd.any_contains(
                "restarting backend, resume list: ['cfg_a', 'cfg_b']")

        assert calls == ['restart']
        # both workers stopped for the restart
        for config in ('cfg_a', 'cfg_b'):
            state = manager.state[config]
            assert state.state == 'restarting'
            assert state.pending_restart is True
            assert state.process is None
        assert_worker_gone(list(manager.state.values()))
        assert_recv_thread_gone(['cfg_a', 'cfg_b'])

        # the resume file written after the wait carries the final list
        assert len(credentials) == 1
        token, _, checksum = credentials[0].partition('-')
        file = GRACEFUL_RESTART.resume_file_of(token)
        assert file.isfile()
        assert GRACEFUL_RESTART.resume_checksum(token, file.atomic_read_bytes()) == checksum
        record = read_record(file)
        assert record.configs == ['cfg_a', 'cfg_b']

        # phase: shutting-down (the backend restart was the last step)
        assert RestartSource().data['phase'] == 'shutting-down'
        # the gate stays on the success path: no worker may start until the
        # process exits (it would be killed and never resumed)
        success, msg = manager.worker_start('WorkerTestScheduler', 'cfg_new')
        assert not success
        assert 'gracefully restarting' in msg

    @pytest.mark.trio
    async def test_timeout_escalates_to_kill_with_resume(self, project_root, manager, monkeypatch):
        monkeypatch.setattr(restart, 'GRACEFUL_STOP_TIMEOUT', 0.5)
        state = start_worker(manager, 'WorkerTestInfinite', 'cfg_inf')

        calls = []
        original = manager.worker_kill

        def spy(config, restart_resume=False):
            calls.append((config, restart_resume))
            return original(config, restart_resume=restart_resume)

        monkeypatch.setattr(manager, 'worker_kill', spy)

        async def fake_lifespan_restart():
            return None

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        credentials = []
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', credentials.append)

        started = time.monotonic()
        await run_graceful_restart(manager)
        duration = time.monotonic() - started

        # the escalation used restart_resume=True and the worker is parked
        assert calls == [('cfg_inf', True)]
        assert duration >= 0.5
        assert state.state == 'restarting'
        assert state.pending_restart is True

        token = credentials[0].partition('-')[0]
        assert read_record(GRACEFUL_RESTART.resume_file_of(token)).configs == ['cfg_inf']

    @pytest.mark.trio
    async def test_no_workers_no_file(self, project_root, manager, monkeypatch):
        calls = []

        async def fake_lifespan_restart():
            calls.append('restart')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        credentials = []
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', credentials.append)

        started = time.monotonic()
        with logger.mock_capture_writer() as capture:
            await run_graceful_restart(manager)
            assert capture.fd.any_contains('waiting for: []')
        # no worker: no wait, no file, no credential, just the restart
        assert time.monotonic() - started < 2
        assert calls == ['restart']
        assert credentials == []
        assert GRACEFUL_RESTART.iter_resume_files() == []

    @pytest.mark.trio
    async def test_actions_write_file_without_workers(self, project_root, manager, monkeypatch):
        async def fake_lifespan_restart():
            return None

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', lambda credential: None)

        hooks = restart.RestartHooks(actions=['test_action'])
        await run_graceful_restart(manager, hooks)

        files = GRACEFUL_RESTART.iter_resume_files()
        assert len(files) == 1
        record = read_record(files[0])
        assert record.configs == []
        assert record.actions == ['test_action']

    @pytest.mark.trio
    async def test_hooks_order_and_actions(self, project_root, manager, monkeypatch):
        start_worker(manager, 'WorkerTestScheduler', 'cfg_a')
        order = []

        async def on_all_stopped():
            # the hook runs after every worker stopped, before the restart
            assert manager.state['cfg_a'].state == 'restarting'
            order.append('hook')

        async def fake_lifespan_restart():
            order.append('restart')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', lambda credential: None)

        hooks = restart.RestartHooks(on_all_stopped=on_all_stopped, actions=['test_action'])
        await run_graceful_restart(manager, hooks)

        assert order == ['hook', 'restart']
        record = read_record(GRACEFUL_RESTART.iter_resume_files()[0])
        assert record.actions == ['test_action']
        assert record.configs == ['cfg_a']

    @pytest.mark.trio
    async def test_hook_failure_cancels_the_restart(self, project_root, manager, monkeypatch):
        start_worker(manager, 'WorkerTestScheduler', 'cfg_a')

        async def failing_hook():
            raise RuntimeError('replacement failed')

        async def fake_lifespan_restart():
            raise AssertionError('the backend must not restart')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', lambda credential: None)

        hooks = restart.RestartHooks(on_all_stopped=failing_hook)
        with pytest.raises(RuntimeError, match='replacement failed'):
            await run_graceful_restart(manager, hooks)

        # cancelled: no resume file, the gate is released, entries are back to idle
        assert GRACEFUL_RESTART.iter_resume_files() == []
        assert manager.restart_aborted() is True
        assert 'cfg_a' not in manager.state
        assert RestartSource().data == {}

    @pytest.mark.trio
    async def test_restart_failure_cancels_and_removes_file(self, project_root, manager, monkeypatch):
        start_worker(manager, 'WorkerTestScheduler', 'cfg_a')

        async def failing_restart():
            raise PermissionError('Cannot restart backend running without supervisor')

        monkeypatch.setattr(restart, 'lifespan_restart', failing_restart)
        credentials = []
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', credentials.append)

        with pytest.raises(PermissionError):
            await run_graceful_restart(manager)

        # the credential was announced before the failure, the cancel removed
        # the file (owner=restart) and released the manager
        assert credentials
        assert GRACEFUL_RESTART.iter_resume_files() == []
        assert manager.restart_aborted() is True
        assert 'cfg_a' not in manager.state

    @pytest.mark.trio
    async def test_cancel_during_wait_writes_nothing(self, project_root, manager, monkeypatch):
        monkeypatch.setattr(restart, 'GRACEFUL_STOP_TIMEOUT', 60.0)
        # a worker that ignores scheduler-stopping keeps the wait open
        start_worker(manager, 'WorkerTestInfinite', 'cfg_inf')

        calls = []

        async def fake_lifespan_restart():
            calls.append('restart')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        credentials = []
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', credentials.append)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(run_graceful_restart, manager)
            await wait_until(lambda: manager.state['cfg_inf'].state == 'scheduler-stopping',
                             description='the worker to enter scheduler-stopping')
            # nothing written while the wait is running
            assert GRACEFUL_RESTART.iter_resume_files() == []
            await cancel_graceful_restart('test cancel', manager)
            # no sleep needed: the nursery exit waits for the orchestration task

        assert calls == []
        assert credentials == []
        assert GRACEFUL_RESTART.iter_resume_files() == []
        assert manager.restart_aborted() is True
        # the gate is released: a new worker may start
        success, msg = manager.worker_start('WorkerTestScheduler', 'cfg_after')
        assert success, msg

    @pytest.mark.trio
    async def test_cancel_during_write_drops_the_file(self, project_root, manager, monkeypatch):
        """
        F3: a force restart landing inside the write window leaves no intent

        The publication is one critical section of the task-held lock: the write
        and its credential are one step inside it, and the withdrawal needs the
        same lock, so the cancel lets the publication run to completion (it must
        not return while the write is still in flight, asserted below), removes
        the file and retracts the credential -- the retraction is the last word
        the supervisor hears. Without the fix the write survived the cancel,
        credential included, so the forced restart resumed the very workers it
        promised to drop.
        """
        start_worker(manager, 'WorkerTestScheduler', 'cfg_a')

        entered = threading.Event()
        release = threading.Event()
        original_write = restart.atomic_write

        def parked_write(file, payload):
            # the write window: the file is not on disk yet, the cancel lands here
            entered.set()
            assert release.wait(5), 'the test never released the write'
            return original_write(file, payload)

        monkeypatch.setattr(restart, 'atomic_write', parked_write)

        async def fake_lifespan_restart():
            raise AssertionError('the forced restart owns the restart, not the orchestration')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        credentials = []
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', credentials.append)
        writer_done = threading.Event()
        published = []
        original_write_resume = GRACEFUL_RESTART.write_resume

        async def spy_write_resume(*args, **kwargs):
            try:
                credential = await original_write_resume(*args, **kwargs)
                published.append(credential)
                return credential
            finally:
                # the publication section is released here: whatever it
                # published is visible to the assertions below
                writer_done.set()

        monkeypatch.setattr(GRACEFUL_RESTART, 'write_resume', spy_write_resume)

        cancel_done = trio.Event()

        async def force_cancel():
            await cancel_graceful_restart('force restart', manager)
            cancel_done.set()

        async with trio.open_nursery() as nursery:
            nursery.start_soon(run_graceful_restart, manager)
            # the worker stopped, the write thread sits inside the window
            await wait_until(entered.is_set, description='the write to start')
            nursery.start_soon(force_cancel)
            # the cancel interrupts the transaction (and drops its scope) before
            # it reaches the withdrawal
            await wait_until(manager.restart_aborted,
                             description='the cancel to interrupt the transaction')
            # the publication section cannot be interrupted: the cancel waits for
            # the publication to run to completion instead of returning mid-write
            assert not cancel_done.is_set(), 'the cancel returned while the write was in flight'
            release.set()
            # the parked write finishes and hands its file over, the withdrawal
            # removes it before the cancel returns
            await wait_until(cancel_done.is_set, description='the cancel to settle after the write')
            await wait_until(writer_done.is_set, description='the interrupted write to return')

        # the cancelled write published its credential, the cancel retracted it
        # last: the force restart resumes nothing, whatever the file does
        writer_credential, = published
        assert credentials == [writer_credential, '']
        assert GRACEFUL_RESTART.iter_resume_files() == []
        assert GRACEFUL_RESTART.resume_file is None
        assert manager.restart_aborted() is True

    @pytest.mark.trio
    async def test_publish_waiting_for_the_lock_is_cancelled(self, project_root, manager, monkeypatch):
        """
        The publication lock is held by the task, never by a pool thread: a
        write that is still waiting for the section when the cancel lands is
        cancelled at the acquire (a checkpoint) and never reaches the disk

        This is the window the task-held lock closes structurally: with the lock
        taken inside the pool thread, the cancel could be over before the write
        ran, and the write would then publish a file plus a live credential with
        nobody left to take them back.
        """
        start_worker(manager, 'WorkerTestScheduler', 'cfg_a')

        writes = []
        original_write = restart.atomic_write

        def spy_write(file, payload):
            writes.append(file)
            return original_write(file, payload)

        monkeypatch.setattr(restart, 'atomic_write', spy_write)

        async def fake_lifespan_restart():
            raise AssertionError('the forced restart owns the restart, not the orchestration')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        credentials = []
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', credentials.append)

        # hold the publication section: the write of the restart has to wait
        await GRACEFUL_RESTART._publication_lock.acquire()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(run_graceful_restart, manager)
            # the workers stopped: the orchestration is at (or about to enter)
            # the publication section
            await wait_until(
                lambda: manager.state.get('cfg_a') is not None
                and manager.state['cfg_a'].state == 'restarting',
                description='the worker to be restarting')
            # the orchestration is parked on the publication section
            await wait_until(
                lambda: GRACEFUL_RESTART._publication_lock.statistics().tasks_waiting == 1,
                description='the write to wait for the publication section')
            nursery.start_soon(cancel_graceful_restart, 'force restart', manager)
            await wait_until(manager.restart_aborted,
                             description='the cancel to interrupt the transaction')
            # the transaction is cancelled: releasing the section cannot make the
            # write run any more, its task is cancelled before it publishes
            GRACEFUL_RESTART._publication_lock.release()

        # the write never reached the disk and nothing was announced: no file, no
        # credential, nothing to resume
        assert writes == []
        assert credentials == []
        assert GRACEFUL_RESTART.iter_resume_files() == []
        assert GRACEFUL_RESTART.resume_file is None
        assert manager.restart_aborted() is True

    @pytest.mark.trio
    async def test_cancel_removes_written_file(self, project_root):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a'])
        token = credential.partition('-')[0]
        assert GRACEFUL_RESTART.resume_file_of(token).isfile()

        await cancel_graceful_restart('test cancel')

        assert not GRACEFUL_RESTART.resume_file_of(token).isfile()

    @pytest.mark.trio
    async def test_failed_removal_still_retracts(self, project_root, monkeypatch):
        """
        The retraction is what seals the cancel: a file the removal could not
        take away (a lock on the file, Windows) is inert from then on, because
        the credential the write had announced was taken back
        """
        credentials = []
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', credentials.append)
        await GRACEFUL_RESTART.write_resume(['cfg_a'])
        file = GRACEFUL_RESTART.resume_file
        assert file.isfile()

        def locked_file(path):
            raise OSError('the file is locked by another process')

        monkeypatch.setattr(restart, 'atomic_remove', locked_file)
        with logger.mock_capture_writer() as capture:
            await cancel_graceful_restart('test cancel')
            assert capture.fd.any_contains('Failed to remove the resume file')

        # the file survives, the intent does not: the last credential the
        # supervisor heard is the retraction, and a backend reading with it
        # resumes nothing
        assert file.isfile()
        assert credentials[-1] == ''
        monkeypatch.setenv(RESUME_TOKEN_ENV, credentials[-1])
        assert GRACEFUL_RESTART.read_resume() is None

    @pytest.mark.trio
    async def test_cancel_keeps_update_owned_file(self, project_root, monkeypatch):
        """
        Only the publication of the restart transaction is withdrawn: the file of
        the in-app update transaction keeps its file and its credential (F7)
        """
        credentials = []
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', credentials.append)
        await GRACEFUL_RESTART.write_resume(['cfg_a'], owner='update')
        file = GRACEFUL_RESTART.resume_file
        assert file.isfile()
        assert len(credentials) == 1

        with logger.mock_capture_writer() as capture:
            await cancel_graceful_restart('test cancel')
            assert not capture.fd.any_contains('Resume file removed by the cancel')

        # neither the file nor the credential of the update transaction is touched
        assert file.isfile()
        assert GRACEFUL_RESTART.resume_file == file
        assert len(credentials) == 1


class TestCancelLog:
    """The cancellation is reported only when it interrupted something"""

    @pytest.mark.trio
    async def test_idle_backend_reports_no_cancel(self, project_root, manager):
        """A force restart (or a backend stop) of an idle backend cancels nothing"""
        with logger.mock_capture_writer() as capture:
            await cancel_graceful_restart('force restart', manager)
            # nothing was in flight: the cleanup ran, but no restart was cancelled
            assert not capture.fd.any_contains('Graceful restart cancelled')
            assert GRACEFUL_RESTART.restart_in_progress() is False

    @pytest.mark.trio
    async def test_in_flight_restart_reports_cancel(self, project_root, manager, monkeypatch):
        """A restart interrupted by a force restart is reported"""
        monkeypatch.setattr(restart, 'GRACEFUL_STOP_TIMEOUT', 60.0)
        # a worker that ignores scheduler-stopping keeps the wait open
        start_worker(manager, 'WorkerTestInfinite', 'cfg_inf')

        async def fake_lifespan_restart():
            raise AssertionError('the backend must not restart')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        monkeypatch.setattr(GRACEFUL_RESTART, 'announce_resume_token', lambda credential: None)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(run_graceful_restart, manager)
            await wait_until(lambda: manager.state['cfg_inf'].state == 'scheduler-stopping',
                             description='the worker to enter scheduler-stopping')
            assert GRACEFUL_RESTART.restart_in_progress() is True
            with logger.mock_capture_writer() as capture:
                await cancel_graceful_restart('force restart', manager)
                assert capture.fd.any_contains('[Restart] Graceful restart cancelled: force restart')
            # no sleep needed: the nursery exit waits for the orchestration task

        # the cancel reached the manager: nothing may be resumed after the restart
        assert manager.restart_aborted() is True


# ============================================================================
# Resume (new backend)
# ============================================================================

class TestResumeAfterRestart:
    """resume_after_restart: read once, queue, start with an interval"""

    @pytest.mark.trio
    async def test_resume_starts_queued_workers(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a', 'cfg_b'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1, 'cfg_b': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.05)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        await resume_after_restart(manager)

        # the file was consumed and deleted by the read
        assert GRACEFUL_RESTART.iter_resume_files() == []
        for config in ('cfg_a', 'cfg_b'):
            state = manager.state[config]
            assert state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        # the phase is cleared when the queue is done
        assert RestartSource().data == {}

    @pytest.mark.trio
    async def test_cleanup_runs_after_the_read(self, manager, monkeypatch):
        """The stale file cleanup is ordered after the read, in the same task"""
        order = []

        def fake_read():
            order.append('read')
            return None

        def fake_cleanup():
            order.append('cleanup')

        monkeypatch.setattr(GRACEFUL_RESTART, 'read_resume', fake_read)
        monkeypatch.setattr(GRACEFUL_RESTART, 'resume_cleanup', fake_cleanup)

        await resume_after_restart(manager)

        assert order == ['read', 'cleanup']

    @pytest.mark.trio
    async def test_no_credential_starts_nothing(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a'])
        file = GRACEFUL_RESTART.resume_file_of(credential.partition('-')[0])
        monkeypatch.delenv(RESUME_TOKEN_ENV, raising=False)

        await resume_after_restart(manager)

        assert manager.state == {}
        # the file is left alone (no credential -> never read)
        assert file.isfile()

    @pytest.mark.trio
    async def test_bad_checksum_deletes_and_starts_nothing(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a'])
        token = credential.partition('-')[0]
        monkeypatch.setenv(RESUME_TOKEN_ENV, f'{token}-0000000000000000')

        await resume_after_restart(manager)

        assert manager.state == {}
        # the read file is deleted even when the verification fails
        assert not GRACEFUL_RESTART.resume_file_of(token).isfile()

    @pytest.mark.trio
    async def test_queue_order_and_interval(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a', 'cfg_b', 'cfg_c'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1, 'cfg_b': 1, 'cfg_c': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.2)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        starts = []
        original = manager.worker_resume

        def spy(mod, config, *args, **kwargs):
            starts.append((config, time.monotonic()))
            return original(mod, config)

        monkeypatch.setattr(manager, 'worker_resume', spy)

        await resume_after_restart(manager)

        assert [config for config, _ in starts] == ['cfg_a', 'cfg_b', 'cfg_c']
        gaps = [starts[i + 1][1] - starts[i][1] for i in range(len(starts) - 1)]
        assert all(gap >= 0.2 * 0.8 for gap in gaps), f'interval not respected: {gaps}'
        for config in ('cfg_a', 'cfg_b', 'cfg_c'):
            assert manager.state[config].wait_running(timeout=WORKER_STARTUP_TIMEOUT)

    @pytest.mark.trio
    async def test_queued_cancel_skips_config(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a', 'cfg_b', 'cfg_c'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1, 'cfg_b': 1, 'cfg_c': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.02)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        original = manager.worker_resume

        def spy(mod, config, *args, **kwargs):
            if config == 'cfg_a':
                # the user cancels cfg_b while it is still queued
                success, msg = manager.worker_kill('cfg_b')
                assert success, msg
            return original(mod, config)

        monkeypatch.setattr(manager, 'worker_resume', spy)

        await resume_after_restart(manager)

        assert manager.state['cfg_a'].wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        assert manager.state['cfg_c'].wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        assert 'cfg_b' not in manager.state

    @pytest.mark.trio
    async def test_config_gone_is_dropped(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a', 'cfg_b'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1, 'cfg_b': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.02)

        def failing_get_mod(config):
            if config == 'cfg_b':
                raise RuntimeError('config gone')
            return 'WorkerTestScheduler'

        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', failing_get_mod)

        await resume_after_restart(manager)

        # cfg_a resumed, cfg_b dropped (not stuck in "resuming")
        assert manager.state['cfg_a'].wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        assert 'cfg_b' not in manager.state

    @pytest.mark.trio
    async def test_missing_from_scan_is_abandoned(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a', 'cfg_b'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        # the scan only knows cfg_a: cfg_b is abandoned after the wait
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: FakeScan({'cfg_a': 1}))
        monkeypatch.setattr(restart, 'RESUME_CONFIG_WAIT', 0.1)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.02)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        await resume_after_restart(manager)

        assert manager.state['cfg_a'].wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        assert 'cfg_b' not in manager.state
        assert GRACEFUL_RESTART.iter_resume_files() == []

    @pytest.mark.trio
    async def test_actions_run_before_resume(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a'], actions=['test_action'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.02)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        order = []
        monkeypatch.setitem(restart.RESUME_ACTIONS, 'test_action', lambda: order.append('action'))
        original = manager.worker_resume

        def spy(mod, config, *args, **kwargs):
            order.append('resume')
            return original(mod, config)

        monkeypatch.setattr(manager, 'worker_resume', spy)

        await resume_after_restart(manager)

        assert order == ['action', 'resume']

    @pytest.mark.trio
    async def test_unknown_action_logged(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume([], actions=['no_such_action'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: FakeScan({}))

        with logger.mock_capture_writer() as capture:
            await resume_after_restart(manager)
            assert capture.fd.any_contains('Unknown resume action ignored: no_such_action')

        assert manager.state == {}
        assert GRACEFUL_RESTART.iter_resume_files() == []

    @pytest.mark.trio
    async def test_cancel_during_resume_stops_queue(self, project_root, manager, monkeypatch):
        credential = await GRACEFUL_RESTART.write_resume(['cfg_a', 'cfg_b', 'cfg_c'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1, 'cfg_b': 1, 'cfg_c': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.5)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(resume_after_restart, manager)
            await wait_until(
                lambda: manager.state.get('cfg_a') is not None
                and manager.state['cfg_a'].state in ('starting', 'running'),
                description='the first queued worker to start')
            # the rest is still queued (the 0.5s start interval has not elapsed)
            assert manager.state['cfg_b'].state == 'resuming'
            await cancel_graceful_restart('test cancel', manager)
            # no sleep needed: the nursery exit waits for the resume task

        # the queue stopped: no further worker was started and the queued
        # entries were dropped by the cancel
        assert 'cfg_b' not in manager.state
        assert 'cfg_c' not in manager.state


async def wait_until(predicate, timeout=5.0, interval=0.01, description='condition'):
    """
    Wait until the predicate holds (event driven, no fixed sleep)

    The orchestration runs as a trio task, so a fixed sleep both wastes time
    and races: this returns as soon as the state appears and fails loudly when
    it never does.

    Args:
        predicate (callable): Returns True once the expected state is there
        timeout (float): Seconds to wait at most
        interval (float): Poll interval
        description (str): What is being waited for, used in the error
    """
    deadline = trio.current_time() + timeout
    while trio.current_time() < deadline:
        if predicate():
            return
        await trio.sleep(interval)
    raise AssertionError(f'Timeout waiting for {description}')


def _fake_get_mod(config):
    """
    Resolve every test config to the scheduler test mod (queue tests)

    Args:
        config (str): Config name

    Returns:
        str: Test mod name
    """
    return 'WorkerTestScheduler'
