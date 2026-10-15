"""
Tests for the update check module (alasio/backend/app/restart/update_check.py):
the per-mod state pipeline, the automatic sequence, the manual check with its
lock and fresh window, and the pause / restore discipline of the maintenance
flows.

The deploy job of a mod is a stand-in: the module only asks it for the check
verdict, the real job is covered by tests/deploy_dev/pack.
"""
import pytest
import trio

from alasio.backend.app.restart import update_check as update_check_module
from alasio.backend.app.restart.update_check import (
    BUSY_MESSAGE, CHECK_INTERVAL, DISABLED_MESSAGE, FLOW_STATES, FRESH_MESSAGE, FRESH_WINDOW, NO_SUPERVISOR_MESSAGE,
    UNMANAGED_MESSAGE, ModUpdateCheck, UpdateCheck
)
from alasio.backend.topic.update import UpdateSource
from alasio.logger import logger


class FakeEntry:
    def __init__(self, mirrors=None):
        self.mirrors = mirrors


class FakeMod:
    def __init__(self, name, mirrors='https://example.com/alas'):
        self.name = name
        self.root = f'/mods/{name}'
        self.entry = FakeEntry(mirrors)


class FakeCheck:
    def __init__(self, uptodate, local='v1', latest='v2'):
        self._uptodate = uptodate
        self.local = local
        self.latest = latest

    def uptodate(self):
        return self._uptodate


class FakeJob:
    """Deploy job stand-in: check() returns the given verdict"""

    def __init__(self, uptodate=True, error=None):
        self.uptodate_result = uptodate
        self.error = error
        self.checks = 0

    async def check(self):
        self.checks += 1
        if self.error is not None:
            raise self.error
        return FakeCheck(self.uptodate_result)


def make_mod(name='m', mirrors='https://example.com/alas', job=None):
    """
    A ModUpdateCheck with a faked job (the update source comes from the mod
    entry, parsed once at construction)

    Args:
        name (str): Mod name
        mirrors: Value of the mod entry mirrors, None for an unmanaged mod
        job (FakeJob): The job the mod hands out

    Returns:
        tuple[UpdateCheck, ModUpdateCheck]
    """
    module = UpdateCheck()
    state = ModUpdateCheck(module, FakeMod(name, mirrors=mirrors))
    job = job if job is not None else FakeJob()

    async def fake_job():
        return job
    state.job = fake_job
    module.mods[name] = state
    return module, state


@pytest.fixture(autouse=True)
def clean_sources():
    """Drop the Update topic singleton around every test"""
    UpdateSource.singleton_clear()
    yield
    UpdateSource.singleton_clear()


class TestState:
    """The state of one mod and its publication on the Update topic"""

    def test_set_state_pushes_a_new_instance(self):
        module, state = make_mod()
        first = state.info
        changed = state.set_state('checking')
        assert changed is not first
        assert state.info is changed
        assert UpdateSource().data == {'m': changed}
        # the versions of the previous state are kept when they are not given
        state.set_state('available', current='v1', latest='v2')
        assert state.info.current_version == 'v1'
        state.set_state('downloading')
        assert (state.info.current_version, state.info.latest_version) == ('v1', 'v2')
        state.set_state('error', error='boom')
        assert state.info.error == 'boom'

    def test_initial_states(self):
        module = UpdateCheck()
        with logger.mock_capture_writer() as capture:
            module.mods = {
                'none': ModUpdateCheck(module, FakeMod('none', mirrors=None)),
                'bad': ModUpdateCheck(module, FakeMod('bad', mirrors='bad')),
                'ok': ModUpdateCheck(module, FakeMod('ok', mirrors='https://example.com/alas')),
            }
        # a malformed declaration is reported once, at construction
        assert capture.fd.any_contains('Invalid update source of "bad"')
        module.set_initial_states()
        # a mod without usable mirrors is unmanaged for good: it is never
        # checked, its state is published once
        assert module.state_of('none') == 'unmanaged'
        assert module.state_of('bad') == 'unmanaged'
        assert module.state_of('ok') == 'idle'
        assert UpdateSource().data['none'].state == 'unmanaged'
        assert module.names() == ['ok']

    def test_initial_states_with_the_mod_updates_off(self):
        """
        The switch (Deploy.Update.AutoUpdate = false) classifies every managed
        mod 'disabled': nothing checks it. A mod without a usable update source
        stays 'unmanaged' -- the source is the classification that outlives
        the switch
        """
        module = UpdateCheck()
        module.mods = {
            'none': ModUpdateCheck(module, FakeMod('none', mirrors=None)),
            'ok': ModUpdateCheck(module, FakeMod('ok', mirrors='https://example.com/alas')),
        }
        module.auto = False
        module.set_initial_states()
        assert module.state_of('none') == 'unmanaged'
        assert module.state_of('ok') == 'disabled'
        assert UpdateSource().data['ok'].state == 'disabled'
        # a mod without a source is not a disabled mod: the source keeps the
        # mod unmanaged whatever the switch says
        assert module.mods['none'].disabled is False
        assert module.mods['ok'].disabled is True
        # the mod still has its update source: the state of the process is not
        # a property of the mod
        assert module.names() == ['ok']

    def test_initial_states_without_a_supervisor(self, monkeypatch):
        """
        A backend without a live supervisor cannot apply an update (only a
        supervisor spawns the new process an applied update takes effect in):
        every mod with an update source is 'no-supervisor', a mod without one
        stays 'unmanaged' -- the environment gate is the outermost one
        """
        monkeypatch.setattr(update_check_module, 'has_supervisor', lambda: False)
        module = UpdateCheck()
        module.mods = {
            'none': ModUpdateCheck(module, FakeMod('none', mirrors=None)),
            'ok': ModUpdateCheck(module, FakeMod('ok', mirrors='https://example.com/alas')),
        }
        module.set_initial_states()
        assert module.state_of('none') == 'unmanaged'
        assert module.state_of('ok') == 'no-supervisor'
        assert UpdateSource().data['ok'].state == 'no-supervisor'
        # a mod without a source is not a no-supervisor mod: the source keeps
        # the mod unmanaged whatever the environment says
        assert module.mods['none'].no_supervisor is False
        assert module.mods['ok'].no_supervisor is True
        # the mod still has its update source, like a disabled one
        assert module.names() == ['ok']
        # the gate outranks the switch: a backend without a supervisor is not
        # 'disabled' (turning AutoUpdate back on would not help)
        module.auto = False
        module.set_initial_states()
        assert module.state_of('ok') == 'no-supervisor'

    def test_names_and_available(self):
        module, state = make_mod()
        module.mods['none'] = ModUpdateCheck(module, FakeMod('none', mirrors=None))
        assert module.names() == ['m']
        assert module.names(managed=False) == ['m', 'none']
        assert module.available() == []
        state.set_state('available')
        assert module.available() == ['m']
        assert module.state_of('nope') == ''
        assert module.state_of('m') == 'available'


class TestReleaseStates:
    """The transient states of the flow in flight (released by its wrap-up)"""

    def test_the_flow_states_are_the_pending_ones(self):
        # the states the apply steps set on a mod while the flow runs
        assert FLOW_STATES == ('queued', 'stopping', 'downloading')

    @pytest.mark.parametrize("state", ['queued', 'stopping', 'downloading'])
    def test_the_pending_states_return_to_available(self, state):
        module, mod = make_mod()
        mod.set_state(state, current='v1', latest='v2', checked_at=123)
        module.release_states()
        assert mod.info.state == 'available'
        # the versions of the last check are the answer of the mod again
        assert (mod.info.current_version, mod.info.latest_version, mod.info.checked_at) == \
            ('v1', 'v2', 123)
        assert module.available() == ['m']

    @pytest.mark.parametrize("state", [
        'unmanaged', 'no-supervisor', 'disabled', 'idle', 'checking', 'uptodate',
        'available', 'updating', 'restart-required', 'error',
    ])
    def test_the_other_states_stay(self, state):
        module, mod = make_mod()
        error = 'boom' if state == 'error' else ''
        mod.set_state(state, error=error)
        module.release_states()
        # the states of the files, the failures and the states of the check
        # module itself do not describe the flow in flight
        assert mod.info.state == state
        assert mod.info.error == error


class TestMirrors:
    """The update source: parsed once, unusable ones make the mod unmanaged"""

    def test_the_mirrors_are_parsed_once(self):
        module = UpdateCheck()
        mod = FakeMod('m', mirrors='https://example.com/alas')
        state = ModUpdateCheck(module, mod)
        assert state.mirrors is not None
        assert list(state.mirrors.urls.values()) == ['https://example.com/alas']
        # the parse happens at construction: a change of the entry afterwards
        # is not picked up (a mount never changes while the process runs)
        mod.entry.mirrors = None
        assert state.mirrors is not None

    def test_a_malformed_declaration_makes_the_mod_unmanaged(self):
        module = UpdateCheck()
        with logger.mock_capture_writer() as capture:
            state = ModUpdateCheck(module, FakeMod('m', mirrors='bad'))
        assert state.mirrors is None
        assert capture.fd.any_contains('Invalid update source of "m"')

    def test_no_mirrors_is_unmanaged_without_a_warning(self):
        module = UpdateCheck()
        with logger.mock_capture_writer() as capture:
            state = ModUpdateCheck(module, FakeMod('m', mirrors=None))
        assert state.mirrors is None
        assert not capture.fd.any_contains('Invalid update source')


class TestCheck:
    """One check of a mod: the state flow around one request"""

    @pytest.mark.trio
    async def test_check_sets_uptodate_or_available(self):
        module, state = make_mod(job=FakeJob(uptodate=True))
        assert await module.update_check('m') == ''
        assert state.info.state == 'uptodate'
        # the timestamp is an int (unix seconds): the sub-second part is not
        # sent to the frontend
        assert isinstance(state.info.checked_at, int)
        assert state.info.checked_at > 0
        assert state.info.latest_version == 'v2'

        async def fake_job():
            return FakeJob(uptodate=False)
        state.job = fake_job
        # the next check is not inside the fresh window of the one that ran
        state._checked_mono = 0.0
        assert await module.update_check('m') == ''
        assert state.info.state == 'available'

    @pytest.mark.trio
    async def test_check_failure_is_reported(self):
        module, state = make_mod(job=FakeJob(error=OSError('mirror is down')))
        assert await module.update_check('m') == ''
        assert state.info.state == 'error'
        assert 'mirror is down' in state.info.error

    @pytest.mark.trio
    async def test_a_cancelled_request_keeps_the_previous_state(self):
        module, state = make_mod()
        state.set_state('available', current='v1', latest='v2')
        previous = state.info

        async def cancelled():
            # the request scope caught the manual cancel
            return None

        state._request_check = cancelled
        assert await module.update_check('m') == ''
        assert state.info is previous
        # a cancelled request is not a completed check: the fresh window of the
        # next one does not apply
        assert state._checked_mono == 0.0

    @pytest.mark.trio
    async def test_a_dropped_sequence_puts_the_state_of_the_last_check_back(self):
        """
        The sequence is dropped while its request is in flight (a pause of a
        maintenance flow, a replacement): the interrupted check records nothing
        and the verdict of the last check goes back -- on the mod and on the
        topic, which must not stay on 'checking' until the next check
        """
        module, state = make_mod()
        state.set_state('available', current='v1', latest='v2')
        verdict = state.info
        entered = trio.Event()

        async def in_flight():
            entered.set()
            await trio.sleep_forever()

        state._request_check = in_flight
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.first_delay = lambda: 0.0
            state.start_check_loop()
            await entered.wait()
            assert state.info.state == 'checking'
            assert UpdateSource().data['m'].state == 'checking'

            module.pause()
            await trio.testing.wait_all_tasks_blocked()
            assert state.info is verdict
            assert state.info.state == 'available'
            assert UpdateSource().data['m'] is verdict
            assert state.running is False
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_a_state_written_meanwhile_is_not_clobbered_by_the_rollback(self):
        """
        A maintenance flow pausing the sequence and marking its scope 'queued' in
        the same section (the ordering of FlowUpdate): the newer state is the
        truth, the rollback of the interrupted check leaves it alone
        """
        module, state = make_mod()
        state.set_state('available')
        entered = trio.Event()

        async def in_flight():
            entered.set()
            await trio.sleep_forever()

        state._request_check = in_flight
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.first_delay = lambda: 0.0
            state.start_check_loop()
            await entered.wait()
            assert state.info.state == 'checking'
            # the flow: pause, then set the state of its scope, no checkpoint
            # in between (the dropped sequence unwinds afterwards)
            module.pause()
            state.set_state('queued')
            await trio.testing.wait_all_tasks_blocked()
            assert state.info.state == 'queued'
            assert UpdateSource().data['m'].state == 'queued'
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_cancel_check_interrupts_the_request(self):
        module, state = make_mod()
        started = trio.Event()

        class SlowJob:
            async def check(self):
                started.set()
                await trio.sleep_forever()

        async def fake_job():
            return SlowJob()
        state.job = fake_job
        assert state.cancel_check() is False
        state.set_state('available')
        results = []
        async with trio.open_nursery() as nursery:
            nursery.start_soon(_collect, results, module.update_check('m'))
            await started.wait()
            assert state.cancel_check() is True
            await trio.testing.wait_all_tasks_blocked()
        # the request was interrupted alone: nothing was recorded and the state
        # of the last check is back
        assert results == ['']
        assert state.info.state == 'available'


class TestRhythm:
    """The delay of the first check and the interval"""

    def test_first_delay_is_the_one_interval_spread(self):
        module = UpdateCheck()
        for _ in range(20):
            delay = module.first_delay()
            # the spread is CHECK_INTERVAL to CHECK_INTERVAL * 2, the fixed
            # window that keeps the sequences evenly spread
            assert CHECK_INTERVAL <= delay <= CHECK_INTERVAL * 2
        assert module.interval() == CHECK_INTERVAL

    @pytest.mark.trio
    async def test_a_disabled_mod_never_starts_a_sequence(self):
        """
        The mod updates off (Deploy.Update.AutoUpdate = false): a disabled mod
        has no sequence at all (the automatic sequence is the automatic check
        itself), nothing checks it -- like an unmanaged mod
        """
        module, state = make_mod()
        module.auto = False
        assert state.disabled is True
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            # every entry refuses it: the start of the mod, of the module and
            # a restore all leave it without a sequence
            state.start_check_loop()
            module.start()
            module.restore()
            await trio.testing.wait_all_tasks_blocked()
            assert state.running is False
            assert (await module.mods['m'].job()).checks == 0
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_a_mod_without_a_supervisor_never_starts_a_sequence(self, monkeypatch):
        """
        The backend cannot replace itself without a parent process: its mod
        updates are off, no sequence is started for the mod -- like a disabled
        one
        """
        monkeypatch.setattr(update_check_module, 'has_supervisor', lambda: False)
        module, state = make_mod()
        assert state.no_supervisor is True
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            # every entry refuses it: the start of the mod, of the module and
            # a restore all leave it without a sequence
            state.start_check_loop()
            module.start()
            module.restore()
            await trio.testing.wait_all_tasks_blocked()
            assert state.running is False
            assert (await module.mods['m'].job()).checks == 0
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_the_loop_waits_the_spread_then_the_interval(self, autojump_clock):
        module, state = make_mod()
        stamps = []
        three = trio.Event()

        async def check():
            stamps.append(trio.current_time())
            if len(stamps) >= 3:
                three.set()

        state._run_update_check = check
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            start = trio.current_time()
            state.start_check_loop()
            # the clock jumps over the sleeps: the stamps are exact
            await three.wait()
            state.stop_check_loop()
            nursery.cancel_scope.cancel()
        # the first check waits the spread, then every check is CHECK_INTERVAL
        # apart (the fixed rhythm of a sequence)
        assert CHECK_INTERVAL <= stamps[0] - start <= CHECK_INTERVAL * 2
        assert stamps[1] - stamps[0] == pytest.approx(CHECK_INTERVAL)
        assert stamps[2] - stamps[1] == pytest.approx(CHECK_INTERVAL)


class TestStartPauseRestore:
    """The pause / restore discipline of the maintenance flows"""

    @pytest.mark.trio
    async def test_start_starts_one_sequence_per_mod(self):
        module, state = make_mod()
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.start()
            assert state.running is True
            await trio.testing.wait_all_tasks_blocked()
            nursery.cancel_scope.cancel()
        assert state._loop_scope is None

    @pytest.mark.trio
    async def test_a_sequence_started_and_stopped_in_the_same_section(self):
        module, state = make_mod()
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            # the task did not start yet: the stop is not lost
            state.start_check_loop()
            state.stop_check_loop()
            await trio.testing.wait_all_tasks_blocked()
            assert state._loop_scope is None
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_pause_stops_and_restore_starts(self):
        module, state = make_mod()
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.start()
            assert state.running is True

            module.pause()
            assert state.running is False
            # pausing an already paused mod changes nothing
            module.pause()
            assert state.running is False

            module.restore()
            assert state.running is True

            # a restore without a pause leaves the running sequence alone
            current = state._loop_scope
            module.restore()
            assert state._loop_scope is current
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_restore_recovers_a_sequence_that_ended_by_itself(self):
        module, state = make_mod()
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.start()
            module.pause()
            # the sequence ended (a check raised, or it was dropped): a restore
            # starts a fresh one
            assert state.running is False
            module.restore()
            assert state.running is True
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_pause_of_an_unknown_mod_is_ignored(self):
        module, state = make_mod()
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.pause(['nope', 'm'])
            assert state.running is False
            module.restore(['nope', 'm'])
            assert state.running is True
            nursery.cancel_scope.cancel()


class TestManualCheck:
    """The manual check of one mod"""

    @pytest.mark.trio
    async def test_the_manual_check_runs_the_check_and_restarts_the_sequence(self):
        module, state = make_mod()
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            message = await module.update_check('m')
            assert message == ''
            assert state.info.state == 'uptodate'
            # the sequence started over (it waits the spread before its check)
            assert state._loop_scope is not None
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_the_manual_check_drops_the_sequence_in_flight(self):
        module, state = make_mod()
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.start()
            first = state._loop_scope
            await module.update_check('m')
            assert state._loop_scope is not first
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_a_fresh_check_is_discarded(self):
        module, state = make_mod()
        assert await module.update_check('m') == ''
        # the check that just ran is the answer already
        assert await module.update_check('m') == FRESH_MESSAGE
        assert await module.update_check('m') == FRESH_MESSAGE

    @pytest.mark.trio
    async def test_the_fresh_window_expires(self):
        module, state = make_mod()
        assert await module.update_check('m') == ''
        state._checked_mono -= FRESH_WINDOW
        assert await module.update_check('m') == ''

    @pytest.mark.trio
    async def test_a_check_in_flight_answers_busy(self):
        module, state = make_mod()

        class SlowJob:
            async def check(self):
                await trio.sleep_forever()
                return FakeCheck(True)

        async def fake_job():
            return SlowJob()
        state.job = fake_job
        started = trio.Event()

        async def first():
            started.set()
            return await module.update_check('m')

        async with trio.open_nursery() as nursery:
            results = []
            nursery.start_soon(_collect, results, first())
            await started.wait()
            await trio.testing.wait_all_tasks_blocked()
            # the second caller is answered without touching the lock
            assert await module.update_check('m') == BUSY_MESSAGE
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_the_second_check_of_the_lock_is_discarded(self):
        module, state = make_mod()
        # two callers pass the pre-checks (the lock is free for both), the
        # re-check inside the lock discards the loser
        calls = []

        async def request():
            calls.append('check')
            return FakeCheck(True)

        state._request_check = request
        first = await module.update_check('m')
        second = await module.update_check('m')
        assert (first, second) == ('', FRESH_MESSAGE)
        assert calls == ['check']

    @pytest.mark.trio
    async def test_check_of_an_unknown_mod_is_refused(self):
        module, state = make_mod()
        with pytest.raises(ValueError, match='No such mod'):
            await module.update_check('nope')

    @pytest.mark.trio
    async def test_check_of_an_unmanaged_mod_answers_the_message(self):
        module, state = make_mod()
        module.mods['none'] = ModUpdateCheck(module, FakeMod('none', mirrors=None))
        # nothing is an error: the mod has no update source, its state stays
        # 'unmanaged' and no request is made
        assert await module.update_check('none') == UNMANAGED_MESSAGE
        assert await module.mods['none'].update_check() == UNMANAGED_MESSAGE
        assert module.mods['none'].info.state == 'idle'

    @pytest.mark.trio
    async def test_an_unmanaged_mod_never_starts_a_sequence(self):
        module, state = make_mod()
        none = ModUpdateCheck(module, FakeMod('none', mirrors=None))
        module.mods['none'] = none
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            # both entries refuse it: the start does nothing, the module's
            # start / restore skip it (names() only lists managed mods)
            none.start_check_loop()
            module.start()
            module.restore()
            assert none.running is False
            assert state.running is True
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_check_of_a_disabled_mod_answers_the_message(self):
        """
        The mod updates off (Deploy.Update.AutoUpdate = false): a disabled mod
        is never checked, not even by hand -- its classification is as final as
        the update source of an unmanaged mod
        """
        module, state = make_mod()
        module.auto = False
        module.set_initial_states()
        assert await module.update_check('m') == DISABLED_MESSAGE
        assert await state.update_check() == DISABLED_MESSAGE
        # no request was made and the state stays where the startup put it
        assert (await module.mods['m'].job()).checks == 0
        assert state.info.state == 'disabled'

    @pytest.mark.trio
    async def test_check_of_a_mod_without_a_supervisor_answers_the_message(self, monkeypatch):
        """
        The backend cannot replace itself (no live supervisor): its mod updates
        are off, the manual check answers the message and no request is made
        """
        monkeypatch.setattr(update_check_module, 'has_supervisor', lambda: False)
        module, state = make_mod()
        module.set_initial_states()
        assert await module.update_check('m') == NO_SUPERVISOR_MESSAGE
        assert await state.update_check() == NO_SUPERVISOR_MESSAGE
        # no request was made and the state stays where the startup put it
        assert (await module.mods['m'].job()).checks == 0
        assert state.info.state == 'no-supervisor'

    @pytest.mark.trio
    async def test_cancel_check_of_an_unknown_mod_is_false(self):
        module, state = make_mod()
        assert module.cancel_check('nope') is False


async def _collect(results, coro):
    """Await a coroutine and collect its result (a task helper)"""
    results.append(await coro)


async def start_module(module, nursery):
    """
    Run the module's task the way the lifespan does, and wait for its nursery

    The module owns the nursery of the sequences (``run``): its first section
    sets it, and the synchronous entries of a test (start / pause / restore)
    read it right away

    Args:
        module (UpdateCheck): The module to run
        nursery (trio.Nursery): The nursery of the test, the parent of the task
    """
    nursery.start_soon(module.run)
    await trio.testing.wait_all_tasks_blocked()


class TestClose:
    """The teardown of the module and the task that owns it"""

    @pytest.mark.trio
    async def test_close_stops_every_sequence(self):
        module, state = make_mod()
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.start()
            assert state._loop_scope is not None
            module.start()
            assert state._loop_scope is not None
            await module.close()
            assert state._loop_scope is None
            nursery.cancel_scope.cancel()

    @pytest.mark.trio
    async def test_run_owns_the_nursery_and_tears_down_when_it_ends(self):
        """
        run() owns the nursery of the sequences: when the lifespan cancels the
        task, the sequences are joined and the shared http client closed -- the
        teardown is the task's finally, not the caller's job
        """
        module, state = make_mod()
        closed = []

        class FakeClient:
            async def aclose(self):
                # read at close time: the sequences were already joined
                closed.append(state.running)

        module._client = FakeClient()
        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.start()
            assert module.nursery is not None
            assert state.running is True
            nursery.cancel_scope.cancel()
        # the nursery of the test only ends once the module's teardown ran
        assert module.nursery is None
        assert state.running is False
        assert closed == [False]

    @pytest.mark.trio
    async def test_close_is_final(self):
        """
        The close is final: the closed client stays, a request arriving after
        it fails through that client instead of a second client being built
        (nothing would ever close the second one)
        """
        module, state = make_mod()

        class FakeClient:
            async def aclose(self):
                pass

        client = FakeClient()
        module._client = client
        await module.close()
        assert module._client is client
        assert module.client() is client


class TestRun:
    """The lifespan task of the module (the nursery the sequences run in)"""

    @pytest.mark.trio
    async def test_the_nursery_is_cleared_when_the_teardown_starts(self):
        """
        The module serves no sequence from the teardown on: the nursery field
        is cleared before the sequences are cancelled and joined, so a restore
        landing in that window (the wrap-up of a flow during the shutdown)
        starts nothing. The field is what the entries read, and a task spawned
        into the closing nursery would only be swallowed by the cancellation
        """
        module, state = make_mod()
        joining = trio.Event()

        async def sequence():
            try:
                await trio.sleep_forever()
            except trio.Cancelled:
                # hold the teardown open: the module is joining this task
                with trio.CancelScope(shield=True):
                    await joining.wait()
                raise

        async with trio.open_nursery() as nursery:
            await start_module(module, nursery)
            module.nursery.start_soon(sequence)
            await trio.testing.wait_all_tasks_blocked()
            nursery.cancel_scope.cancel()
            with trio.CancelScope(shield=True):
                try:
                    await trio.testing.wait_all_tasks_blocked()
                    # the teardown is in flight (the sequence is being joined) and
                    # the module already serves no sequence
                    assert module.nursery is None
                    module.restore()
                    assert state._loop_scope is None
                finally:
                    # never leave the held task waiting, even on a failure
                    joining.set()

    @pytest.mark.trio
    async def test_the_nursery_is_set_before_a_sibling_spawns_its_own_tasks(self):
        """
        The lifespan starts this task and the restart manager in one section:
        both are runnable in the same pass, and a task spawned during that pass
        (the startup flow, which starts the sequences) only runs in the next
        one -- so the nursery is set by then, whatever order the pass picks
        (trio randomizes the order of the tasks of a pass). The wiring of the
        lifespan depends on this: an unset nursery silently starts no sequence
        """
        module = UpdateCheck()
        seen = []

        async def child():
            seen.append(module.nursery is not None)

        async def sibling():
            # the shape of the manager: its first section spawns the task that
            # starts the sequences
            async with trio.open_nursery() as nursery:
                nursery.start_soon(child)

        async with trio.open_nursery() as nursery:
            nursery.start_soon(module.run)
            nursery.start_soon(sibling)
            await trio.testing.wait_all_tasks_blocked()
            nursery.cancel_scope.cancel()
        assert seen == [True]
