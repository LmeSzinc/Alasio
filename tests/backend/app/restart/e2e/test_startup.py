"""
Scenario two of the realcase runs: the process startup

The real ``FlowStartup`` over the real backend of the conftest: consume the
resume file of the previous process, run the full update the previous process
did not just run, start the check sequences and release the configs of the
resume queue. The branches: a cold start with and without an update (the
convergence handover), a fresh and a stale resume record, the updates switched
off, a backend without a supervisor, a broken mirror, the user requests during
the startup window, the resume actions and the resume queue taken over by a
new flow.

Every test asserts the complete log of its run, see the conftest docstring.
"""
import builtins
import threading

import pytest
import trio

from alasio.backend.app.restart import restart_step as step_module
from alasio.backend.app.restart.restart_base import STARTING_UP, FlowRefused
from alasio.backend.app.restart.resume_file import RESUME_TOKEN_ENV
from alasio.backend.topic import worker as worker_module
from alasio.logger import logger

from .conftest import (
    MockWorkerProcess, assert_logs, at, credential_of, read_record, rendered, wait_log, wait_until, write_resume
)


@pytest.mark.trio
async def test_a_cold_start_with_an_update_converges_the_tree(make_backend):
    """
    A cold start finds an update: the full update applies it and the flow
    hands the process over -- the new process sees the fresh check in the
    record, the chain converges in one update and one restart
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    backend.servers.publish_update()
    mod_root = backend.project.mod_root('WorkerTestScheduler')
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Update] Updating mod "WorkerTestScheduler"
INFO | [CurrentVersion] v1
INFO | [LatestVersion] v2
INFO | Update available: v1 -> v2
INFO | Updating files to "{mod_root}"
INFO | Replacing files to "{mod_root}"
INFO | Update done
INFO | [Update] Applied the update of "WorkerTestScheduler"
INFO | [Update] Full update finished, changed mods: ['WorkerTestScheduler'], failed mods: []
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 0 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "startup" flow (startup convergence): 0 configs to resume
INFO | [Restart] Flow "startup" finished
""")
            assert backend.manager.frozen is True
            assert backend.restart.calls == 1
            assert read_record(backend).configs == []
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_cold_start_with_a_current_tree_starts_the_checks(make_backend):
    """
    A cold start with nothing to apply: the full update validates the tree
    (no change, no handover) and the check sequences start
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    mod_root = backend.project.mod_root('WorkerTestScheduler')
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Update] Updating mod "WorkerTestScheduler"
INFO | [CurrentVersion] v1
INFO | [LatestVersion] v1
INFO | Already up to date: v1
INFO | Resetting files to "{mod_root}"
INFO | Reset done
INFO | [Update] Full update finished, changed mods: [], failed mods: []
INFO | [Restart] Flow "startup" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
            assert backend.manager.frozen is False
            assert backend.restart.calls == 0
            assert backend.update_check.mods['WorkerTestScheduler'].running is True
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_new_process_resumes_the_workers_of_the_resume_file(make_backend, monkeypatch):
    """
    The full handover chain of two processes: the first one hands over with a
    running config, the second one consumes the record and starts the worker
    again (a new worker process)
    """
    # process one: a user restart hands the backend over with the running config
    first = make_backend()
    await first.install('WorkerTestScheduler')
    async with trio.open_nursery() as nursery:
        await first.start(nursery)
        await first.start_worker('cfg_a')
        with logger.mock_capture_writer() as capture:
            first.manager.restart()
            await first.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Restart] Flow "restart" accepted
INFO | [WorkerManager] Requesting scheduler stop: cfg_a
INFO | [Restart] Stopping the workers, waiting up to 600.0s for: ['cfg_a']
INFO | [WorkerManager] Worker stopped: cfg_a, exitcode=0
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {first.resume_file} (owner=restart, 1 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "restart" flow (user request): 1 configs to resume
INFO | [Restart] Flow "restart" finished
""")
        record = read_record(first)
        assert record.configs == ['cfg_a']
        assert record.updated_all_at == at(2, 0)
        # the credential the supervisor injects into the new process
        monkeypatch.setenv(RESUME_TOKEN_ENV, credential_of(first))
        nursery.cancel_scope.cancel()

    # process two: the startup consumes the record and the queue resumes it
    second = make_backend()
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await second.start(nursery)
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Restart] Resume intent accepted: 1 configs, 0 actions, owner=restart, updated_all_at={at(2, 0):.0f}
INFO | [Restart] Resume queue recorded: ['cfg_a']
INFO | [Restart] The previous process checked less than 300s ago: the startup runs no full update
INFO | [Restart] Flow "startup" finished
INFO | [Restart] Resume queue: 1 configs, starting them one by one
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
""")
            # the queue waited for the report of the worker (the log above ends
            # with it): the new process of the chain reached the running state
            # on its own
            await second.wait_running('cfg_a')
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_fresh_resume_record_skips_the_full_update(make_backend, monkeypatch):
    """
    The process this one replaces checked a moment ago (the daily restart
    checks right before its handover): the startup does not repeat the check
    and carries the completion time on
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    credential = await write_resume(backend, ['cfg_a'], updated_all_at=at(2, 0))
    monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Restart] Resume intent accepted: 1 configs, 0 actions, owner=restart, updated_all_at={at(2, 0):.0f}
INFO | [Restart] Resume queue recorded: ['cfg_a']
INFO | [Restart] The previous process checked less than 300s ago: the startup runs no full update
INFO | [Restart] Flow "startup" finished
INFO | [Restart] Resume queue: 1 configs, starting them one by one
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
""")
            assert backend.daily.updated_all_at == at(2, 0)
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_an_old_resume_record_runs_the_full_update(make_backend, monkeypatch):
    """
    The record is older than the check window: the tree is converged now (a
    record that only says "someone checked hours ago" is no answer about the
    tree as it is now)
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    credential = await write_resume(backend, ['cfg_a'], updated_all_at=at(2, 0) - 3600.0)
    monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
    mod_root = backend.project.mod_root('WorkerTestScheduler')
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Restart] Resume intent accepted: 1 configs, 0 actions, owner=restart, updated_all_at={at(2, 0) - 3600.0:.0f}
INFO | [Restart] Resume queue recorded: ['cfg_a']
INFO | [Update] Updating mod "WorkerTestScheduler"
INFO | [CurrentVersion] v1
INFO | [LatestVersion] v1
INFO | Already up to date: v1
INFO | Resetting files to "{mod_root}"
INFO | Reset done
INFO | [Update] Full update finished, changed mods: [], failed mods: []
INFO | [Restart] Flow "startup" finished
INFO | [Restart] Resume queue: 1 configs, starting them one by one
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
""")
            assert backend.daily.updated_all_at == at(2, 0)
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_mod_updates_off_skip_the_full_update(make_backend, deploy_config):
    """
    Deploy.Update.AutoUpdate = false: every mod is 'disabled', the startup
    checks and applies nothing and is never handed over for an update
    """
    # the switch the process reads at startup
    deploy_config.auto_update = False
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    backend.servers.publish_update()
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            assert_logs(capture, """\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Restart] The mod updates are disabled: the startup runs no full update
INFO | [Restart] Flow "startup" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
            assert backend.restart.calls == 0
            assert backend.update_check.state_of('WorkerTestScheduler') == 'disabled'
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_backend_without_a_supervisor_runs_no_full_update(make_backend, monkeypatch):
    """
    No parent process can spawn the new backend: an applied update could not
    take effect, so nothing is checked, applied or handed over -- and the
    restart entry answers the same reason up front
    """
    monkeypatch.delattr(builtins, '__mpipe_conn__', raising=False)
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    backend.servers.publish_update()
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            with pytest.raises(FlowRefused) as e:
                backend.manager.restart()
            assert str(e.value) == 'The backend runs without a supervisor: it cannot restart itself'
            assert_logs(capture, """\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Restart] The backend runs without a supervisor: the startup runs no full update
INFO | [Restart] Flow "startup" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
            assert backend.restart.calls == 0
            assert backend.update_check.state_of('WorkerTestScheduler') == 'no-supervisor'
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_broken_mirror_leaves_the_startup_running(make_backend):
    """
    The mirror is down (every request is a 404): the startup reports the mod,
    records no change and keeps running -- the check sequences retry later
    """
    backend = make_backend(mods={'WorkerTestScheduler': 'http://broken.mock'})
    await backend.install('WorkerTestScheduler')
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            assert_logs(capture, """\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Update] Updating mod "WorkerTestScheduler"
INFO | [CurrentVersion] v1
WARNING | Request to "http://broken.mock/latest.pack" failed (attempt 1/3): HTTPStatusError: Client error '404 Not Found' for url 'http://broken.mock/latest.pack'
ERROR | [Update] Failed to update "WorkerTestScheduler": Client error '404 Not Found' for url 'http://broken.mock/latest.pack'
ERROR | HTTPStatusError: Client error '404 Not Found' for url 'http://broken.mock/latest.pack'
<traceback>
INFO | [Update] Full update finished, changed mods: [], failed mods: ['WorkerTestScheduler']
INFO | [Restart] Flow "startup" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
            assert backend.restart.calls == 0
            assert backend.update_check.state_of('WorkerTestScheduler') == 'error'
            assert backend.update_check.mods['WorkerTestScheduler'].running is True
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_user_requests_are_refused_while_the_backend_starts_up(make_backend):
    """
    The startup window: both user entries answer "starting up" (the process is
    not served by a user flow yet), the startup runs on
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    backend.servers.publish_update()
    gate = trio.Event()
    # the full update of the startup hangs on the mirror
    backend.servers.hold('update_v1.pack', gate)
    mod_root = backend.project.mod_root('WorkerTestScheduler')
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(backend.manager.run)
            await backend.servers.hold_entered.wait()
            assert backend.manager.flow_name == 'startup'
            for entry in (backend.manager.update_apply, backend.manager.restart):
                with pytest.raises(FlowRefused) as e:
                    entry()
                assert str(e.value) == STARTING_UP
            gate.set()
            await backend.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Update] Updating mod "WorkerTestScheduler"
INFO | [CurrentVersion] v1
INFO | [LatestVersion] v2
INFO | Update available: v1 -> v2
INFO | Updating files to "{mod_root}"
INFO | Replacing files to "{mod_root}"
INFO | Update done
INFO | [Update] Applied the update of "WorkerTestScheduler"
INFO | [Update] Full update finished, changed mods: ['WorkerTestScheduler'], failed mods: []
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 0 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "startup" flow (startup convergence): 0 configs to resume
INFO | [Restart] Flow "startup" finished
""")
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_resume_actions_of_the_record_run_before_the_resume(make_backend, monkeypatch):
    """
    The record carries action tags (a cleanup a flow wants the new process to
    run before its resume): they run in order, an unknown tag is reported and
    skipped
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    ran = []
    monkeypatch.setitem(step_module.RESUME_ACTIONS, 'test-cleanup', lambda: ran.append('cleanup'))
    credential = await write_resume(backend, ['cfg_a'], actions=['test-cleanup', 'no-such-action'],
                                    updated_all_at=at(2, 0))
    monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Restart] Resume intent accepted: 1 configs, 2 actions, owner=restart, updated_all_at={at(2, 0):.0f}
INFO | [Restart] Running resume action: test-cleanup
WARNING | [Restart] Unknown resume action ignored: no-such-action
INFO | [Restart] Resume queue recorded: ['cfg_a']
INFO | [Restart] The previous process checked less than 300s ago: the startup runs no full update
INFO | [Restart] Flow "startup" finished
INFO | [Restart] Resume queue: 1 configs, starting them one by one
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
""")
            assert ran == ['cleanup']
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_resume_queue_stops_when_a_new_flow_takes_the_backend_over(make_backend, monkeypatch):
    """
    The resume queue drains the configs of the new process; a user restart
    claims the backend while the queue is inside the start of the second config:
    the new flow collects that entry (its resume list owns it from then on), the
    start finds a worker no queue may resume any more and is skipped, and the
    handover records both configs for the process after it
    """
    backend = make_backend(configs={'cfg_a': 'WorkerTestScheduler', 'cfg_b': 'WorkerTestScheduler'})
    await backend.install('WorkerTestScheduler')
    credential = await write_resume(backend, ['cfg_a', 'cfg_b'], updated_all_at=at(2, 0))
    monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
    # the queue is held inside the start of the second config (the test owns the
    # moment its mod resolves): no timing window is guessed
    held = threading.Event()
    real_get_mod = worker_module.get_mod

    def get_mod(config):
        if config == 'cfg_b':
            held.wait()
        return real_get_mod(config)

    monkeypatch.setattr(worker_module, 'get_mod', get_mod)
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(backend.manager.run)
            # the first config is started and reported, the queue is inside the
            # start of the second one
            await wait_log(capture, 'Worker resumed: cfg_a')
            backend.manager.restart()
            await wait_log(capture, 'Flow "restart" finished')
            held.set()
            await backend.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Restart] Resume intent accepted: 2 configs, 0 actions, owner=restart, updated_all_at={at(2, 0):.0f}
INFO | [Restart] Resume queue recorded: ['cfg_a', 'cfg_b']
INFO | [Restart] The previous process checked less than 300s ago: the startup runs no full update
INFO | [Restart] Flow "startup" finished
INFO | [Restart] Resume queue: 2 configs, starting them one by one
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
INFO | [Restart] Flow "restart" accepted
INFO | [WorkerManager] Requesting scheduler stop: cfg_a
INFO | [Restart] Stopping the workers, waiting up to 600.0s for: ['cfg_a']
INFO | [WorkerManager] Worker stopped: cfg_a, exitcode=0
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 2 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "restart" flow (user request): 2 configs to resume
INFO | [Restart] Flow "restart" finished
INFO | [Restart] Resume skipped: "cfg_b": Worker is not awaiting auto-resume: "cfg_b"
""")
            assert read_record(backend).configs == ['cfg_a', 'cfg_b']
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_queue_waits_for_each_worker_to_report_itself(make_backend, monkeypatch):
    """
    Every start of the resume queue ends with the report of the worker itself
    (the report is what the manager sees the process through): the second
    config of the queue starts only after the first worker reported itself
    """
    backend = make_backend(configs={'cfg_a': 'WorkerTestScheduler', 'cfg_b': 'WorkerTestScheduler'})
    await backend.install('WorkerTestScheduler')
    credential = await write_resume(backend, ['cfg_a', 'cfg_b'], updated_all_at=at(2, 0))
    monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
    # the test holds the report of the first worker (see REPORT_GATE): the queue
    # parks in the wait of the start of "cfg_a" and the moment the report
    # arrives is the test's to choose
    MockWorkerProcess.REPORT_GATE.clear()
    try:
        with logger.mock_capture_writer() as capture:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(backend.manager.run)
                await wait_log(capture, 'Resuming worker: cfg_a')
                # the queue is parked in the wait of the report of the worker: the
                # entry is the "starting" the manager marked and the second config
                # of the queue did not start while the report was missing
                await wait_until(lambda: backend.worker_manager.state['cfg_a'].state == 'starting')
                assert 'Resuming worker: cfg_b' not in rendered(capture)
                MockWorkerProcess.REPORT_GATE.set()
                await wait_log(capture, 'Resuming worker: cfg_b')
                await backend.wait_quiet()
                assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Restart] Resume intent accepted: 2 configs, 0 actions, owner=restart, updated_all_at={at(2, 0):.0f}
INFO | [Restart] Resume queue recorded: ['cfg_a', 'cfg_b']
INFO | [Restart] The previous process checked less than 300s ago: the startup runs no full update
INFO | [Restart] Flow "startup" finished
INFO | [Restart] Resume queue: 2 configs, starting them one by one
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
INFO | [WorkerManager] Resuming worker: cfg_b
INFO | [Restart] Worker resumed: cfg_b
""")
                nursery.cancel_scope.cancel()
    finally:
        # a report the test never released must not leave the queue waiting for
        # it: the run would pay the whole RESUME_REPORT_TIMEOUT before it ends
        MockWorkerProcess.REPORT_GATE.set()


@pytest.mark.trio
async def test_the_queue_wakes_the_moment_its_worker_is_taken_over(make_backend, monkeypatch):
    """
    A user restart claims the backend while the resume queue waits for the report
    of a worker: the stop the flow sends moves the entry, the state wakes the
    wait at once -- the worker never reported, the queue stops at the stop of
    the flow instead of sitting out the timeout of the wait
    """
    backend = make_backend(configs={'cfg_a': 'WorkerTestScheduler', 'cfg_b': 'WorkerTestScheduler'})
    await backend.install('WorkerTestScheduler')
    credential = await write_resume(backend, ['cfg_a', 'cfg_b'], updated_all_at=at(2, 0))
    monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
    # the first worker never reports (its report is held): the queue is parked in
    # the wait of the start of "cfg_a" when the flow takes the backend over
    MockWorkerProcess.REPORT_GATE.clear()
    try:
        with logger.mock_capture_writer() as capture:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(backend.manager.run)
                await wait_log(capture, 'Resuming worker: cfg_a')
                backend.manager.restart()
                # the wake-up is part of the stop of the flow: a wait that ended
                # by its timeout (RESUME_REPORT_TIMEOUT, 5s) would not make this
                # bound, and the timeout line is asserted absent below
                await wait_log(capture, 'Resume unconfirmed: "cfg_a"', timeout=1.0)
                await wait_log(capture, 'The resume queue was interrupted by a new flow')
                await wait_log(capture, 'Flow "restart" finished')
                await backend.wait_quiet()
                # the queue wakes inside the stop of the flow, so the two write
                # their lines as it happens: the lines are asserted, not how the
                # two actors interleave them
                assert 'Resume unconfirmed after' not in rendered(capture)
                assert read_record(backend).configs == ['cfg_a', 'cfg_b']
                nursery.cancel_scope.cancel()
    finally:
        # the report the test held leaves no peer of the run parked on the gate
        MockWorkerProcess.REPORT_GATE.set()
