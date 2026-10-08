"""
Tests for UpdateManager / ModUpdateManager (alasio/backend/app/update.py):
the check loop of a mod, the update windows and the update transaction.

The deploy jobs, the worker manager and the graceful restart are faked:
the manager logic (states, windows, cancellation, the transaction
orchestration) runs without network and processes. The fake job plays the
phase contract of the real one (on_job_phase at the 'updating' boundary)
and the tests drive the orchestration side of the fake restart (its
recorded hook), like run_graceful_restart would. The topic source is the
real one, cleared per test.
"""
import pytest
import trio

from alasio.backend.app import update as update_module
from alasio.backend.app.update import UpdateError, UpdateManager
from alasio.backend.topic.update import UpdateSource
from alasio.config.entry.const import ModEntryInfo
from alasio.deploy.pack.job import DeployCheck, UpdateAborted
from alasio.deploy.pack.server_file import LatestInfo


async def wait_until(predicate, timeout=5.0, interval=0.01, description='condition'):
    """
    Wait until the predicate holds (fail loudly when it never does).

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


def state_of(manager, name):
    """
    The state of a mod on the manager, safe before the mods are bound.

    Returns:
        str: The state, '' when the manager has no mod of that name yet
    """
    mod = manager.mods.get(name, None)
    return mod.info.state if mod is not None else ''


def make_check(local='c1', latest='c2', checksum='cs'):
    """
    The DeployCheck a fake job returns.

    Returns:
        DeployCheck: The check result
    """
    return DeployCheck(local=local, info=LatestInfo(version=latest, checksum=checksum))


class FakeMod:
    """Mod stand-in: a name, a root and an entry carrying the mirrors."""

    def __init__(self, name, mirrors='', root=''):
        self.name = name
        self.root = root or f'/{name}'
        self.entry = ModEntryInfo(name=name, mirrors=mirrors)


class FakeJob:
    """
    DeployJob stand-in: every method consults the script (keyed by the root
    of the target) and records the call. update() plays the phase contract of
    the real job: it reports its phases to the manager, so the applying phase
    (the debug pause of the graceful restart) is exercised. A gate of the
    script blocks a call until the test opens it (or the call is cancelled).
    """

    def __init__(self, script, root, name='', server=None):
        self.script = script
        self.root = root
        self.name = name
        self.server = server

    async def check(self):
        self.script.calls.append((self.root, 'check'))
        gate = self.script.check_gate.get(self.root)
        if gate is not None:
            await gate.wait()
        result = self.script.checks.get(self.root, make_check(local='', latest='c1'))
        if isinstance(result, Exception):
            raise result
        return result

    async def update(self, on_job_phase=None):
        self.script.calls.append((self.root, 'update'))
        if on_job_phase is not None:
            # the phase contract of the real job (see DeployJob.update): the
            # returned bool is honored, False aborts the flow
            if not await on_job_phase('downloading'):
                raise UpdateAborted('the transaction was cancelled')
            if not await on_job_phase('updating'):
                raise UpdateAborted('the transaction was cancelled')
        gate = self.script.update_gate.get(self.root)
        if gate is not None:
            await gate.wait()
        result = self.script.updates.get(self.root, True)
        if isinstance(result, Exception):
            raise result
        return result

    async def run_unfinished_job(self):
        self.script.calls.append((self.root, 'unfinished'))
        result = self.script.unfinished.get(self.root, False)
        if isinstance(result, Exception):
            raise result
        return result


class FakeScript:
    """Script and call log of the fake deploy jobs."""

    def __init__(self):
        # {root: DeployCheck | Exception} of check() (the version pre-flight)
        self.checks = {}
        # {root: trio.Event} blocking the check of a root
        self.check_gate = {}
        # {root: bool | Exception} of update() (the apply)
        self.updates = {}
        # {root: trio.Event} blocking the update of a root
        self.update_gate = {}
        # {root: bool | Exception} of run_unfinished_job()
        self.unfinished = {}
        # [(root, method)]
        self.calls = []

    def job(self, root=None, name='', server=None):
        """
        DeployJob stand-in factory (the managers construct jobs this way).
        """
        return FakeJob(self, str(root) if root is not None else '', name, server)

    def calls_of(self, method):
        """
        Returns:
            list[str]: Roots of the recorded calls of one method
        """
        return [root for root, called in self.calls if called == method]


class FakeNursery:
    """Nursery stand-in recording the scheduled tasks."""

    def __init__(self):
        self.started = []

    def start_soon(self, async_fn, *args):
        self.started.append((async_fn, args))


class FakeWorkerManager:
    """WorkerManager stand-in: records the update windows and the releases."""

    def __init__(self):
        self.locked_mods = set()
        self.transaction = ''
        # [(mod, config)] accepted during an update window: served by
        # release_update_queue
        self.queue = []
        self.released = []
        self.resumed = []
        self.dropped = []
        self.killed = []

    def update_lock_mods(self, mods):
        self.locked_mods.update(mods)

    def update_unlock_mods(self, mods):
        self.locked_mods.difference_update(mods)

    def update_begin_transaction(self, mod):
        self.transaction = mod

    def update_end_transaction(self):
        self.transaction = ''

    def release_update_queue(self, mods=None):
        self.released.append(mods)
        return [config for mod, config in self.queue if mods is None or mod in mods]

    def worker_resume(self, mod, config):
        self.resumed.append(config)
        return True, 'Success'

    def drop_resume(self, configs):
        self.dropped.extend(configs)
        return configs

    def worker_force_kill(self, config, restart_resume=False):
        self.killed.append(config)
        return True, 'Success'


@pytest.fixture(autouse=True)
def cleanup_source():
    """Clear the UpdateSource singleton after each test."""
    yield
    UpdateSource.singleton_clear()


@pytest.fixture
def script(monkeypatch):
    """Fake deploy jobs of the manager."""
    script = FakeScript()
    monkeypatch.setattr(update_module, 'DeployJob', script.job)
    return script


@pytest.fixture
def workers(monkeypatch):
    """Fake worker manager of the manager."""
    workers = FakeWorkerManager()
    monkeypatch.setattr(update_module, 'BACKEND_WORKER_MANAGER', workers)
    return workers


@pytest.fixture
def restarts(monkeypatch):
    """Recorded graceful restart requests (the restart itself is covered by
    tests/backend/app/test_restart_resume.py)."""
    requests = []

    async def fake_request(reason='', nursery=None, hooks=None):
        requests.append({'reason': reason, 'nursery': nursery, 'hooks': hooks})

    monkeypatch.setattr(update_module.restart_app, 'request_graceful_restart', fake_request)
    # no interval between two starts of the released queue (the real
    # interval is covered by the restart queue tests)
    monkeypatch.setattr(update_module.restart_app, 'WORKER_START_INTERVAL', 0.0)
    update_module.restart_app.GRACEFUL_RESTART.reset()
    yield requests
    update_module.restart_app.GRACEFUL_RESTART.reset()


@pytest.fixture
def manager():
    """A fresh UpdateManager (the module singleton is only used by the topic)."""
    return UpdateManager()


@pytest.fixture
def supervisor(monkeypatch):
    """A running supervisor (the pipe of the backend)."""
    marker = object()
    monkeypatch.setattr(update_module, 'mpipe_backend', marker)
    return marker


def script_manager(manager, monkeypatch, mods, auto=True, interval=300.0):
    """
    Script the mods and the schedule of a manager (run() reads both).

    Args:
        manager (UpdateManager): Manager to script
        monkeypatch: pytest monkeypatch fixture
        mods (dict[str, FakeMod]): Mods of the manager
        auto (bool): Value of the AutoUpdate config. Defaults to True.
        interval (float | None): Check interval in seconds, None = only
            the first check. Defaults to 300.
    """
    monkeypatch.setattr(manager, 'load_mods', lambda: dict(mods))

    def load_schedule():
        manager.auto = auto
        manager.interval = interval

    monkeypatch.setattr(manager, 'load_schedule', load_schedule)


async def drive_transaction(mod, restarts, expect_restart=True):
    """
    Run run_transaction() with the orchestration side of the fake restart: the
    hook of the recorded request, which the real run_graceful_restart would
    run after its wait. Returns when the transaction task finished.

    Args:
        mod (ModUpdateManager): The mod whose transaction to run
        restarts (list): Recorded requests of the fake request_graceful_restart
        expect_restart (bool): Whether the transaction reaches the applying
            phase (a restart request is expected). Defaults to True.
    """
    done = trio.Event()

    async def run():
        try:
            await mod.run_transaction()
        finally:
            done.set()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(run)
        if expect_restart:
            await wait_until(lambda: len(restarts) == 1, description='the restart request')
            nursery.start_soon(restarts[0]['hooks'].on_all_stopped)
        await done.wait()
        nursery.cancel_scope.cancel()


class TestNextDelay:
    """The pure schedule of the rounds."""

    def test_second_check_random(self, manager):
        manager.auto = True
        manager.interval = 300.0
        delay = manager.next_delay(1)
        assert 300.0 <= delay <= 600.0

    def test_third_check_interval(self, manager):
        manager.auto = True
        manager.interval = 300.0
        assert manager.next_delay(2) == 300.0
        assert manager.next_delay(10) == 300.0

    def test_interval_zero_stops_after_the_followup(self, manager):
        """CheckUpdateInterval = 0 keeps the first check and its follow-up."""
        manager.auto = True
        manager.interval = None
        assert 300.0 <= manager.next_delay(1) <= 600.0
        assert manager.next_delay(2) is None

    def test_auto_off(self, manager):
        """AutoUpdate = false: no automatic check at all."""
        manager.auto = False
        manager.interval = 300.0
        assert manager.next_delay(1) is None
        assert manager.next_delay(2) is None


class TestCheckLoop:
    """The check of the mounted mods."""

    @pytest.mark.trio
    async def test_first_check_immediate_uptodate(self, manager, script, monkeypatch):
        script.checks['/m'] = make_check(local='c1', latest='c1')
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'uptodate', description='uptodate')

            info = manager.mods['m'].info
            assert info.current_version == 'c1'
            assert info.latest_version == 'c1'
            assert info.checked_at > 0
            assert info.error == ''
            # the topic carries the same state
            assert UpdateSource().data['m'].state == 'uptodate'
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_check_available(self, manager, script, monkeypatch):
        script.checks['/m'] = make_check(local='c1', latest='c2')
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'available')
            info = manager.mods['m'].info
            assert info.current_version == 'c1'
            assert info.latest_version == 'c2'
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_check_missing_local_is_available(self, manager, script, monkeypatch):
        script.checks['/m'] = make_check(local='', latest='c1')
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'available')
            assert manager.mods['m'].info.current_version == ''
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_check_failure_is_error(self, manager, script, monkeypatch):
        script.checks['/m'] = RuntimeError('offline')
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'error')
            assert 'offline' in manager.mods['m'].info.error
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_unmanaged_mod_is_not_checked(self, manager, script, monkeypatch):
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'unmanaged')
            assert script.calls_of('check') == []
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_invalid_mirrors_is_error(self, manager, script, monkeypatch):
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors={'': 'not-an-url'})})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'error')
            assert 'Invalid mirror' in manager.mods['m'].info.error
            assert script.calls_of('check') == []
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_check_window_locks_the_mod(self, manager, script, workers, monkeypatch):
        """The window opens during the check and closes after it."""
        gate = trio.Event()
        script.check_gate['/m'] = gate
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: workers.locked_mods == {'m'}, description='the check window')
            assert manager.mods['m'].info.state == 'checking'
            gate.set()
            await wait_until(lambda: workers.locked_mods == set(), description='the window close')
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_cancel_check_restores_the_previous_state(self, manager, script, workers,
                                                            monkeypatch):
        gate = trio.Event()
        script.check_gate['/m'] = gate
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'checking')
            await manager.cancel()
            # the in-flight request is interrupted and the state falls back
            # to the one before the check
            await wait_until(lambda: state_of(manager, 'm') == 'idle')
            assert manager.mods['m'].info.checked_at == 0.
            await wait_until(lambda: workers.locked_mods == set())
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_cancel_without_anything_is_refused(self, manager):
        with pytest.raises(UpdateError, match='Nothing to cancel'):
            await manager.cancel()

    @pytest.mark.trio
    async def test_manual_check_interrupts_the_wait(self, manager, script, monkeypatch):
        """A manual check cancels the pending wait and runs immediately."""
        script.checks['/m'] = make_check(local='c1', latest='c1')
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: len(script.calls_of('check')) == 1)
            # the loop waits the random second delay now; the manual check
            # must interrupt it and run now
            await manager.check('m')
            await wait_until(lambda: len(script.calls_of('check')) == 2)
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_manual_check_with_auto_off(self, manager, script, monkeypatch):
        """Without automatic checks the loop only serves the manual ones."""
        script.checks['/m'] = make_check(local='c1', latest='c2')
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')},
                       auto=False, interval=None)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'idle')
            await trio.sleep(0.05)
            assert script.calls_of('check') == []
            await manager.check('m')
            await wait_until(lambda: state_of(manager, 'm') == 'available')
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_manual_check_selectors(self, manager, script, monkeypatch):
        script_manager(manager, monkeypatch, {
            'a': FakeMod('a', mirrors='https://a.example'),
            'b': FakeMod('b', mirrors=''),
        }, auto=False, interval=None)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'a') == 'idle')
            await wait_until(lambda: state_of(manager, 'b') == 'unmanaged')
            # an unmanaged mod cannot be checked
            with pytest.raises(UpdateError, match='no update source'):
                await manager.check('b')
            with pytest.raises(UpdateError, match='No such mod'):
                await manager.check('nope')
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_check_not_running_manager(self, manager):
        with pytest.raises(UpdateError, match='not running'):
            await manager.check('')


def bind_transaction(manager, script, monkeypatch, supervisor, state='available'):
    """
    Bind a manager to a single mod with an update available (the fixture of
    the transaction tests): the per-mod manager, a fake nursery for the
    transaction task and the state ready for update_apply.
    """
    manager._nursery = FakeNursery()
    manager.bind_mods({'m': FakeMod('m', mirrors='https://a.example')})
    mod = manager.mods['m']
    mod.set_state(state, current='c1', latest='c2')
    script.checks['/m'] = make_check(local='c1', latest='c2')
    return mod


class TestApply:
    """The update transaction."""

    @pytest.mark.trio
    async def test_refusals(self, manager, script, supervisor, monkeypatch):
        bind_transaction(manager, script, monkeypatch, supervisor)
        update_module.restart_app.GRACEFUL_RESTART.reset()

        manager.mods = {}
        with pytest.raises(UpdateError, match='No such mod'):
            await manager.apply('m')
        bind_transaction(manager, script, monkeypatch, supervisor)
        manager.mods['m'].mod.entry.mirrors = ''
        with pytest.raises(UpdateError, match='no update source'):
            await manager.apply('m')
        bind_transaction(manager, script, monkeypatch, supervisor, state='uptodate')
        with pytest.raises(UpdateError, match='no update available'):
            await manager.apply('m')
        bind_transaction(manager, script, monkeypatch, supervisor)
        manager._transaction = 'other'
        with pytest.raises(UpdateError, match='already in progress'):
            await manager.apply('m')
        manager._transaction = ''
        update_module.restart_app.GRACEFUL_RESTART.running = True
        with pytest.raises(UpdateError, match='graceful restart'):
            await manager.apply('m')
        update_module.restart_app.GRACEFUL_RESTART.running = False

    @pytest.mark.trio
    async def test_apply_without_supervisor(self, manager, script, monkeypatch):
        bind_transaction(manager, script, monkeypatch, supervisor=None)
        monkeypatch.setattr(update_module, 'mpipe_backend', None)

        with pytest.raises(UpdateError, match='without supervisor'):
            await manager.apply('m')

    @pytest.mark.trio
    async def test_apply_downloading_to_updating(self, manager, script, workers, restarts,
                                                 supervisor, monkeypatch):
        mod = bind_transaction(manager, script, monkeypatch, supervisor)

        await manager.apply('m')
        # accepted: the window is open and the task is scheduled
        assert mod.info.state == 'downloading'
        assert workers.transaction == 'm'
        assert manager._nursery.started == [(mod.run_transaction, ())]

        await drive_transaction(mod, restarts)
        # the job reported its phases: the restart was requested at the
        # 'updating' boundary and the apply ran with the replace window open
        assert len(restarts) == 1
        assert 'update of mod "m"' in restarts[0]['reason']
        assert script.calls_of('update') == ['/m']
        assert mod.info.state == 'updating'
        # the transaction window stays open until the process exits (the
        # new backend takes the restart over)
        assert workers.transaction == 'm'
        assert manager.applying is True

    @pytest.mark.trio
    async def test_preflight_uptodate_drops_the_update(self, manager, script, workers, restarts,
                                                       supervisor, monkeypatch):
        """The update is gone (another flow applied it): no restart."""
        mod = bind_transaction(manager, script, monkeypatch, supervisor)
        script.checks['/m'] = make_check(local='c2', latest='c2')

        await manager.apply('m')
        await mod.run_transaction()

        assert mod.info.state == 'uptodate'
        assert restarts == []
        assert workers.transaction == ''
        assert script.calls_of('update') == []

    @pytest.mark.trio
    async def test_cancel_downloading(self, manager, script, workers, restarts,
                                      supervisor, monkeypatch):
        mod = bind_transaction(manager, script, monkeypatch, supervisor)
        gate = trio.Event()
        script.check_gate['/m'] = gate
        # a config the user started during the window: released on cancel
        workers.queue = [('m', 'cfg1')]
        monkeypatch.setattr(update_module, 'get_mod', lambda config: manager.mods['m'].mod)

        await manager.apply('m')
        async with trio.open_nursery() as nursery:
            nursery.start_soon(mod.run_transaction)
            await wait_until(lambda: manager._transaction_scope is not None)
            await manager.cancel()
            gate.set()
            await wait_until(lambda: mod.info.state == 'available')
            # zero side effect: nothing applied, the window closed, the
            # queued config started
            assert script.calls_of('update') == []
            assert restarts == []
            assert workers.transaction == ''
            await wait_until(lambda: workers.resumed == ['cfg1'])
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_preflight_failure(self, manager, script, workers, restarts,
                                     supervisor, monkeypatch):
        mod = bind_transaction(manager, script, monkeypatch, supervisor)
        script.checks['/m'] = RuntimeError('offline')
        workers.queue = [('m', 'cfg1')]
        monkeypatch.setattr(update_module, 'get_mod', lambda config: manager.mods['m'].mod)

        await manager.apply('m')
        await mod.run_transaction()

        assert mod.info.state == 'error'
        assert 'offline' in mod.info.error
        assert restarts == []
        assert workers.transaction == ''
        await wait_until(lambda: workers.resumed == ['cfg1'])

    @pytest.mark.trio
    async def test_apply_failure_in_the_job(self, manager, script, workers, restarts,
                                            supervisor, monkeypatch):
        """The apply did not converge: the hook raises, the restart is
        cancelled by the orchestration (here: the test observes the raise)."""
        mod = bind_transaction(manager, script, monkeypatch, supervisor)
        script.updates['/m'] = False

        await manager.apply('m')
        async with trio.open_nursery() as nursery:
            nursery.start_soon(mod.run_transaction)
            await wait_until(lambda: len(restarts) == 1)
            hooks = restarts[0]['hooks']
            with pytest.raises(RuntimeError, match='did not converge'):
                await hooks.on_all_stopped()
            await wait_until(lambda: mod.info.state == 'error')
            nursery.cancel_scope.cancel()

        assert 'Some files failed' in mod.info.error
        assert workers.transaction == ''

    @pytest.mark.trio
    async def test_apply_exception_in_the_job(self, manager, script, workers, restarts,
                                              supervisor, monkeypatch):
        mod = bind_transaction(manager, script, monkeypatch, supervisor)
        script.updates['/m'] = RuntimeError('boom')

        await manager.apply('m')
        async with trio.open_nursery() as nursery:
            nursery.start_soon(mod.run_transaction)
            await wait_until(lambda: len(restarts) == 1)
            hooks = restarts[0]['hooks']
            with pytest.raises(RuntimeError, match='boom'):
                await hooks.on_all_stopped()
            await wait_until(lambda: mod.info.state == 'error')
            nursery.cancel_scope.cancel()

        assert 'boom' in mod.info.error

    @pytest.mark.trio
    async def test_apply_cancel_is_refused(self, manager, script, restarts,
                                           supervisor, monkeypatch):
        """Once the job reached its applying phase the cancel is refused."""
        mod = bind_transaction(manager, script, monkeypatch, supervisor)
        gate = trio.Event()
        script.update_gate['/m'] = gate

        await manager.apply('m')
        async with trio.open_nursery() as nursery:
            nursery.start_soon(mod.run_transaction)
            await wait_until(lambda: len(restarts) == 1)
            nursery.start_soon(restarts[0]['hooks'].on_all_stopped)
            await wait_until(lambda: manager.applying is True)

            with pytest.raises(UpdateError, match='cannot be cancelled'):
                await manager.cancel()

            gate.set()
            await wait_until(lambda: mod.info.state == 'error' or script.calls_of('update'))
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_phase_callback_aborts_a_cancelled_transaction(self, manager, script, workers,
                                                                 restarts, supervisor, monkeypatch):
        """The fallback check of the phase callback: a cancel the job could not
        be interrupted at is answered at the phase boundary, the job aborts
        (UpdateAborted) and the state returns to available."""
        mod = bind_transaction(manager, script, monkeypatch, supervisor)
        gate = trio.Event()
        script.check_gate['/m'] = gate
        workers.queue = [('m', 'cfg1')]
        monkeypatch.setattr(update_module, 'get_mod', lambda config: manager.mods['m'].mod)

        await manager.apply('m')
        async with trio.open_nursery() as nursery:
            nursery.start_soon(mod.run_transaction)
            await wait_until(lambda: manager._transaction_scope is not None)
            # as if update_cancel arrived while the pre-flight was blocked (no
            # in-flight request the cancel could interrupt)
            manager._cancel_requested = True
            gate.set()
            await wait_until(lambda: mod.info.state == 'available')
            nursery.cancel_scope.cancel()

        # the job was aborted at its first phase and never reached the apply
        assert restarts == []
        assert script.calls_of('update') == ['/m']
        assert workers.transaction == ''
        await wait_until(lambda: workers.resumed == ['cfg1'])


class TestConvergence:
    """The startup convergence of a killed process' update (§2.6)."""

    @pytest.mark.trio
    async def test_convergence_restarts(self, manager, script, restarts, monkeypatch):
        script.unfinished['/m'] = True
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: len(restarts) == 1)
            assert 'interrupted update' in restarts[0]['reason']
            assert state_of(manager, 'm') == 'updating'
            # the checks of this process are skipped: the first round of
            # the new process runs them
            await trio.sleep(0.05)
            assert script.calls_of('check') == []
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_convergence_failure_is_retried(self, manager, script, monkeypatch):
        script.unfinished['/m'] = RuntimeError('offline')
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')},
                       auto=False, interval=None)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'error')
            assert 'offline' in manager.mods['m'].info.error
            assert manager.mods['m'].convergence_pending is True
            # the next round retries the convergence first: it still fails
            # and the check of the round is skipped
            await manager.check('m')
            await wait_until(lambda: len(script.calls_of('unfinished')) == 2)
            await trio.sleep(0.05)
            assert script.calls_of('check') == []
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_convergence_nothing_to_finish(self, manager, script, restarts, monkeypatch):
        """No unfinished job: no restart, the checks run as usual."""
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')})

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') in ('uptodate', 'available', 'error'))
            assert restarts == []
            assert script.calls_of('unfinished') == ['/m']
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_convergence_retry_restarts(self, manager, script, restarts, monkeypatch):
        """A retried convergence that succeeds ends in one restart."""
        script.unfinished['/m'] = RuntimeError('offline')
        script_manager(manager, monkeypatch, {'m': FakeMod('m', mirrors='https://a.example')},
                       auto=False, interval=None)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(manager.run)
            await wait_until(lambda: state_of(manager, 'm') == 'error')
            # the next round: the retry succeeds, the convergence restart is
            # requested and the state moves to updating
            script.unfinished['/m'] = True
            await manager.check('m')
            await wait_until(lambda: len(restarts) == 1)
            assert 'interrupted update' in restarts[0]['reason']
            assert state_of(manager, 'm') == 'updating'
            assert manager.mods['m'].convergence_pending is False
            nursery.cancel_scope.cancel()
