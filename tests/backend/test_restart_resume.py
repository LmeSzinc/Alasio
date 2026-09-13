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
import time

import msgspec
import pytest
import trio

from alasio.backend import restart
from alasio.backend.restart import (
    GRACEFUL_RESTART, RESUME_TOKEN_ENV, ResumeRecord, cancel_graceful_restart, read_resume, resume_after_restart,
    resume_checksum, resume_cleanup, run_graceful_restart, write_resume
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

    def test_write_read_roundtrip(self, project_root, monkeypatch):
        credential = write_resume(['cfg_a', 'cfg_b'])
        token, sep, checksum = credential.partition('-')
        assert sep and token and checksum

        file = restart.resume_file(token)
        assert file.isfile()
        payload = file.atomic_read_bytes()
        # the credential's checksum is the HMAC of the exact file content
        assert resume_checksum(token, payload) == checksum
        record = json.loads(payload)
        assert record['owner'] == 'restart'
        assert record['configs'] == ['cfg_a', 'cfg_b']
        assert record['actions'] == []
        assert record['ts'] > 0

        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        read = read_resume()
        assert isinstance(read, ResumeRecord)
        assert read.configs == ['cfg_a', 'cfg_b']
        assert read.owner == 'restart'
        # read once: the file is deleted by the read
        assert not file.isfile()

    def test_write_with_actions_and_owner(self, project_root):
        write_resume([], owner='update', actions=['clear_pycache'])
        files = restart.iter_resume_files()
        assert len(files) == 1
        record = read_record(files[0])
        assert record.configs == []
        assert record.actions == ['clear_pycache']
        assert record.owner == 'update'

    def test_write_removes_leftover_files(self, project_root):
        stale = restart.resume_file('0123456789abcdef')
        stale.atomic_write(b'{"stale": true}')

        write_resume(['cfg_a'])

        assert not stale.isfile()
        assert len(restart.iter_resume_files()) == 1

    def test_read_only_reads_own_token_file(self, project_root, monkeypatch):
        credential = write_resume(['cfg_a'])
        # a foreign file (another token) must be neither read nor deleted
        foreign = restart.resume_file('ffffffffffffffff')
        foreign.atomic_write(b'{"foreign": true}')

        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        read = read_resume()

        assert read is not None
        assert foreign.isfile()

    def test_read_without_credential_touches_nothing(self, project_root, monkeypatch):
        credential = write_resume(['cfg_a'])
        file = restart.resume_file(credential.partition('-')[0])

        monkeypatch.delenv(RESUME_TOKEN_ENV, raising=False)
        assert read_resume() is None
        # the file is not even opened
        assert file.isfile()

    def test_read_missing_file_returns_none(self, project_root, monkeypatch):
        monkeypatch.setenv(RESUME_TOKEN_ENV, '00ff00ff-checksum')
        assert read_resume() is None

    def test_read_malformed_credential_returns_none(self, project_root, monkeypatch):
        file = restart.resume_file('deadbeef')
        file.atomic_write(b'{}')

        monkeypatch.setenv(RESUME_TOKEN_ENV, 'no-separator-here-but-token-is-long')
        # 'no' is not a known token: nothing read, nothing deleted
        assert read_resume() is None
        assert file.isfile()

    def test_read_tampered_payload_rejected_and_deleted(self, project_root, monkeypatch):
        credential = write_resume(['cfg_a'])
        token = credential.partition('-')[0]
        file = restart.resume_file(token)
        payload = bytearray(file.atomic_read_bytes())
        # flip one byte: the checksum must catch it
        payload[-2] = ord(' ') if payload[-2] != ord(' ') else ord('x')
        file.atomic_write(bytes(payload))

        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        assert read_resume() is None
        # a read file never survives, valid or not
        assert not file.isfile()

    def test_read_invalid_payload_rejected(self, project_root, monkeypatch):
        # a payload whose checksum is valid but whose schema is not
        token = 'aabbccdd00112233'
        payload = b'{"unknown": 1}'
        checksum = resume_checksum(token, payload)
        restart.resume_file(token).atomic_write(payload)

        monkeypatch.setenv(RESUME_TOKEN_ENV, f'{token}-{checksum}')
        assert read_resume() is None
        assert not restart.resume_file(token).isfile()

    def test_read_rejects_tampered_with_own_token(self, project_root, monkeypatch):
        """The token alone is not enough: the checksum comes from the credential"""
        credential = write_resume(['cfg_a'])
        token = credential.partition('-')[0]
        # rewrite the file with a different payload under the same token
        restart.resume_file(token).atomic_write(b'{"configs": ["evil"], "owner": "restart", "ts": 0, "actions": []}')

        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        assert read_resume() is None
        assert not restart.resume_file(token).isfile()


class TestResumeCleanup:
    """resume_cleanup removes only stale resume files"""

    def test_removes_only_stale_resume_files(self, project_root, monkeypatch):
        import os

        monkeypatch.setattr(restart, 'RESUME_CLEANUP_AGE', 100.0)
        fresh = restart.resume_file('aaaa1111')
        stale = restart.resume_file('bbbb2222')
        other = restart.resume_folder().joinpath('notes.json')
        for file in (fresh, stale, other):
            file.atomic_write(b'{}')
        old = time.time() - 1000
        os.utime(stale, (old, old))

        with logger.mock_capture_writer() as capture:
            resume_cleanup()
            assert capture.fd.any_contains('[Restart] Resume cleanup: removed 1 stale files, kept 1')

        assert fresh.isfile()
        assert not stale.isfile()
        # unrelated files are not touched
        assert other.isfile()

    def test_missing_folder_is_noop(self, project_root):
        assert not restart.resume_folder().isdir()
        # must not raise, must not create anything
        resume_cleanup()
        assert not restart.resume_folder().isdir()

    def test_empty_folder_is_noop(self, project_root):
        restart.resume_folder().makedirs()
        resume_cleanup()
        assert restart.resume_folder().isdir()


class TestAnnounceResumeToken:
    """announce_resume_token sends the credential over the supervisor pipe"""

    def test_sends_command_resume(self, monkeypatch):
        parent_conn, child_conn = multiprocessing.Pipe()
        monkeypatch.setattr(builtins, '__mpipe_conn__', child_conn, raising=False)
        try:
            restart.announce_resume_token('token123-checksum456')

            assert parent_conn.poll(timeout=1)
            assert parent_conn.recv_bytes() == b'command:resume:token123-checksum456'
        finally:
            parent_conn.close()
            child_conn.close()

    def test_no_supervisor_is_noop(self, monkeypatch):
        monkeypatch.delattr(builtins, '__mpipe_conn__', raising=False)
        restart.announce_resume_token('token123-checksum456')


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
        monkeypatch.setattr(restart, 'announce_resume_token', credentials.append)

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
        file = restart.resume_file(token)
        assert file.isfile()
        assert resume_checksum(token, file.atomic_read_bytes()) == checksum
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
        monkeypatch.setattr(restart, 'announce_resume_token', credentials.append)

        started = time.monotonic()
        await run_graceful_restart(manager)
        duration = time.monotonic() - started

        # the escalation used restart_resume=True and the worker is parked
        assert calls == [('cfg_inf', True)]
        assert duration >= 0.5
        assert state.state == 'restarting'
        assert state.pending_restart is True

        token = credentials[0].partition('-')[0]
        assert read_record(restart.resume_file(token)).configs == ['cfg_inf']

    @pytest.mark.trio
    async def test_no_workers_no_file(self, project_root, manager, monkeypatch):
        calls = []

        async def fake_lifespan_restart():
            calls.append('restart')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        credentials = []
        monkeypatch.setattr(restart, 'announce_resume_token', credentials.append)

        started = time.monotonic()
        with logger.mock_capture_writer() as capture:
            await run_graceful_restart(manager)
            assert capture.fd.any_contains('waiting for: []')
        # no worker: no wait, no file, no credential, just the restart
        assert time.monotonic() - started < 2
        assert calls == ['restart']
        assert credentials == []
        assert restart.iter_resume_files() == []

    @pytest.mark.trio
    async def test_actions_write_file_without_workers(self, project_root, manager, monkeypatch):
        async def fake_lifespan_restart():
            return None

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        monkeypatch.setattr(restart, 'announce_resume_token', lambda credential: None)

        hooks = restart.RestartHooks(actions=['clear_pycache'])
        await run_graceful_restart(manager, hooks)

        files = restart.iter_resume_files()
        assert len(files) == 1
        record = read_record(files[0])
        assert record.configs == []
        assert record.actions == ['clear_pycache']

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
        monkeypatch.setattr(restart, 'announce_resume_token', lambda credential: None)

        hooks = restart.RestartHooks(on_all_stopped=on_all_stopped, actions=['clear_pycache'])
        await run_graceful_restart(manager, hooks)

        assert order == ['hook', 'restart']
        record = read_record(restart.iter_resume_files()[0])
        assert record.actions == ['clear_pycache']
        assert record.configs == ['cfg_a']

    @pytest.mark.trio
    async def test_hook_failure_cancels_the_restart(self, project_root, manager, monkeypatch):
        start_worker(manager, 'WorkerTestScheduler', 'cfg_a')

        async def failing_hook():
            raise RuntimeError('replacement failed')

        async def fake_lifespan_restart():
            raise AssertionError('the backend must not restart')

        monkeypatch.setattr(restart, 'lifespan_restart', fake_lifespan_restart)
        monkeypatch.setattr(restart, 'announce_resume_token', lambda credential: None)

        hooks = restart.RestartHooks(on_all_stopped=failing_hook)
        with pytest.raises(RuntimeError, match='replacement failed'):
            await run_graceful_restart(manager, hooks)

        # cancelled: no resume file, the gate is released, entries are back to idle
        assert restart.iter_resume_files() == []
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
        monkeypatch.setattr(restart, 'announce_resume_token', credentials.append)

        with pytest.raises(PermissionError):
            await run_graceful_restart(manager)

        # the credential was announced before the failure, the cancel removed
        # the file (owner=restart) and released the manager
        assert credentials
        assert restart.iter_resume_files() == []
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
        monkeypatch.setattr(restart, 'announce_resume_token', credentials.append)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(run_graceful_restart, manager)
            await trio.sleep(0.5)
            assert manager.state['cfg_inf'].state == 'scheduler-stopping'
            # nothing written while the wait is running
            assert restart.iter_resume_files() == []
            cancel_graceful_restart('test cancel')
            await trio.sleep(0.3)

        assert calls == []
        assert credentials == []
        assert restart.iter_resume_files() == []
        assert manager.restart_aborted() is True
        # the gate is released: a new worker may start
        success, msg = manager.worker_start('WorkerTestScheduler', 'cfg_after')
        assert success, msg

    def test_cancel_removes_written_file(self, project_root):
        credential = write_resume(['cfg_a'])
        token = credential.partition('-')[0]
        assert restart.resume_file(token).isfile()

        cancel_graceful_restart('test cancel')

        assert not restart.resume_file(token).isfile()


# ============================================================================
# Resume (new backend)
# ============================================================================

class TestResumeAfterRestart:
    """resume_after_restart: read once, queue, start with an interval"""

    @pytest.mark.trio
    async def test_resume_starts_queued_workers(self, project_root, manager, monkeypatch):
        credential = write_resume(['cfg_a', 'cfg_b'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1, 'cfg_b': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.05)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        await resume_after_restart(manager)

        # the file was consumed and deleted by the read
        assert restart.iter_resume_files() == []
        for config in ('cfg_a', 'cfg_b'):
            state = manager.state[config]
            assert state.wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        # the phase is cleared when the queue is done
        assert RestartSource().data == {}

    @pytest.mark.trio
    async def test_no_credential_starts_nothing(self, project_root, manager, monkeypatch):
        credential = write_resume(['cfg_a'])
        file = restart.resume_file(credential.partition('-')[0])
        monkeypatch.delenv(RESUME_TOKEN_ENV, raising=False)

        await resume_after_restart(manager)

        assert manager.state == {}
        # the file is left alone (no credential -> never read)
        assert file.isfile()

    @pytest.mark.trio
    async def test_bad_checksum_deletes_and_starts_nothing(self, project_root, manager, monkeypatch):
        credential = write_resume(['cfg_a'])
        token = credential.partition('-')[0]
        monkeypatch.setenv(RESUME_TOKEN_ENV, f'{token}-0000000000000000')

        await resume_after_restart(manager)

        assert manager.state == {}
        # the read file is deleted even when the verification fails
        assert not restart.resume_file(token).isfile()

    @pytest.mark.trio
    async def test_queue_order_and_interval(self, project_root, manager, monkeypatch):
        credential = write_resume(['cfg_a', 'cfg_b', 'cfg_c'])
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
        credential = write_resume(['cfg_a', 'cfg_b', 'cfg_c'])
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
        credential = write_resume(['cfg_a', 'cfg_b'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1, 'cfg_b': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.02)

        async def failing_get_mod(config):
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
        credential = write_resume(['cfg_a', 'cfg_b'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        # the scan only knows cfg_a: cfg_b is abandoned after the wait
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: FakeScan({'cfg_a': 1}))
        monkeypatch.setattr(restart, 'RESUME_CONFIG_WAIT', 0.1)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.02)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        await resume_after_restart(manager)

        assert manager.state['cfg_a'].wait_running(timeout=WORKER_STARTUP_TIMEOUT)
        assert 'cfg_b' not in manager.state
        assert restart.iter_resume_files() == []

    @pytest.mark.trio
    async def test_actions_run_before_resume(self, project_root, manager, monkeypatch):
        credential = write_resume(['cfg_a'], actions=['clear_pycache'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.02)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        order = []
        monkeypatch.setitem(restart.RESUME_ACTIONS, 'clear_pycache', lambda: order.append('action'))
        original = manager.worker_resume

        def spy(mod, config, *args, **kwargs):
            order.append('resume')
            return original(mod, config)

        monkeypatch.setattr(manager, 'worker_resume', spy)

        await resume_after_restart(manager)

        assert order == ['action', 'resume']

    @pytest.mark.trio
    async def test_unknown_action_logged(self, project_root, manager, monkeypatch):
        credential = write_resume([], actions=['no_such_action'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: FakeScan({}))

        with logger.mock_capture_writer() as capture:
            await resume_after_restart(manager)
            assert capture.fd.any_contains('Unknown resume action ignored: no_such_action')

        assert manager.state == {}
        assert restart.iter_resume_files() == []

    @pytest.mark.trio
    async def test_cancel_during_resume_stops_queue(self, project_root, manager, monkeypatch):
        credential = write_resume(['cfg_a', 'cfg_b', 'cfg_c'])
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
        fake = FakeScan({'cfg_a': 1, 'cfg_b': 1, 'cfg_c': 1})
        monkeypatch.setattr(restart, 'ConfigScanSource', lambda: fake)
        monkeypatch.setattr(restart, 'WORKER_START_INTERVAL', 0.5)
        monkeypatch.setattr('alasio.backend.topic.worker.get_mod', _fake_get_mod)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(resume_after_restart, manager)
            await trio.sleep(0.2)
            # first worker started, the rest is still queued (0.5s interval)
            assert manager.state['cfg_a'].state in ('starting', 'running')
            assert manager.state['cfg_b'].state == 'resuming'
            cancel_graceful_restart('test cancel')
            await trio.sleep(0.3)

        # the queue stopped: no further worker was started and the queued
        # entries were dropped by the cancel
        assert 'cfg_b' not in manager.state
        assert 'cfg_c' not in manager.state


async def _fake_get_mod(config):
    """
    Resolve every test config to the scheduler test mod (queue tests)

    Args:
        config (str): Config name

    Returns:
        str: Test mod name
    """
    return 'WorkerTestScheduler'
