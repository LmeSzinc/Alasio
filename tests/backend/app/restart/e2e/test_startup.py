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

import pytest
import trio

from alasio.backend.app.restart import restart_step as step_module
from alasio.backend.app.restart.restart_base import STARTING_UP, FlowRefused
from alasio.backend.app.restart.resume_file import RESUME_TOKEN_ENV
from alasio.logger import logger

from .conftest import RESUME_INTERVAL, assert_logs, at, credential_of, read_record, wait_log, write_resume


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
INFO | [Update] Full update finished, changed mods: ['WorkerTestScheduler']
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
INFO | [Update] Full update finished, changed mods: []
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
INFO | [Restart] Resume queue: 1 configs, starting them with a {RESUME_INTERVAL}s interval
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
""")
            # the queue does not wait for the worker to report running: the new
            # process of the chain reaches the running state on its own
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
INFO | [Restart] Resume queue: 1 configs, starting them with a {RESUME_INTERVAL}s interval
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
INFO | [Update] Full update finished, changed mods: []
INFO | [Restart] Flow "startup" finished
INFO | [Restart] Resume queue: 1 configs, starting them with a {RESUME_INTERVAL}s interval
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
INFO | [Update] Full update finished, changed mods: []
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
    backend.servers.hold('from_v1.pack', gate)
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
INFO | [Update] Full update finished, changed mods: ['WorkerTestScheduler']
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
INFO | [Restart] Resume queue: 1 configs, starting them with a {RESUME_INTERVAL}s interval
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
""")
            assert ran == ['cleanup']
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_resume_queue_stops_when_a_new_flow_takes_the_backend_over(make_backend, monkeypatch):
    """
    The resume queue drains the configs of the new process; a user restart
    claims the backend while the queue runs: the queue stops -- its remaining
    entries are collected by the new flow and its handover records them for
    the process after it
    """
    backend = make_backend(configs={'cfg_a': 'WorkerTestScheduler', 'cfg_b': 'WorkerTestScheduler'})
    await backend.install('WorkerTestScheduler')
    credential = await write_resume(backend, ['cfg_a', 'cfg_b'], updated_all_at=at(2, 0))
    monkeypatch.setenv(RESUME_TOKEN_ENV, credential)
    # the test claims the restart inside the interval between the two starts of
    # the queue: it needs the real one (the runs shrink it otherwise)
    monkeypatch.setattr(step_module, 'WORKER_START_INTERVAL', 1.0)
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(backend.manager.run)
            # the queue is between two configs (the interval between starts)
            await wait_log(capture, 'Resume queue: 2 configs')
            backend.manager.restart()
            await wait_log(capture, 'Flow "restart" finished')
            await backend.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Restart] Resume intent accepted: 2 configs, 0 actions, owner=restart, updated_all_at={at(2, 0):.0f}
INFO | [Restart] Resume queue recorded: ['cfg_a', 'cfg_b']
INFO | [Restart] The previous process checked less than 300s ago: the startup runs no full update
INFO | [Restart] Flow "startup" finished
INFO | [Restart] Resume queue: 2 configs, starting them with a 1.0s interval
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
INFO | [Restart] The resume queue was interrupted by a new flow
""")
            assert read_record(backend).configs == ['cfg_a', 'cfg_b']
            nursery.cancel_scope.cancel()
