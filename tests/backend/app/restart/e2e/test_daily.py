"""
Scenario three of the realcase runs: the daily maintenance

The real ``DailyState`` (its target read from the real deploy config) and the
real ``FlowDaily``: the clock crosses the configured time and the schedule
claims the daily flow -- or the crossing is served by the startup, by the flow
in flight, or nothing claims it. The branches: the served crossing, the
maintenance of a backend with the updates off, the crossing served by the
update flow in flight (the upgrade), the crossing skipped while the backend
starts up, a failed handover and a backend without a supervisor.

Every test asserts the complete log of its run, see the conftest docstring.
"""
import builtins

import pytest
import trio

from alasio.logger import logger

from .conftest import assert_logs, at, read_record, wait_log


@pytest.mark.trio
async def test_the_schedule_serves_the_crossing_with_the_daily_flow(make_backend, clock):
    """
    The clock walks over the target: the schedule claims the daily flow, which
    stops the real worker, runs the full update and hands the process over
    (the crossing is served by the update of this process)
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    mod_root = backend.project.mod_root('WorkerTestScheduler')
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        with logger.mock_capture_writer() as capture:
            clock.set(at(4, 0))
            nursery.start_soon(backend.daily.run, backend.manager)
            await wait_log(capture, 'Flow "daily" finished')
            await backend.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Schedule] Daily maintenance at 03:50
INFO | [Restart] Flow "daily" accepted
INFO | [Schedule] Daily maintenance started by the schedule ("daily" flow)
INFO | [WorkerManager] Requesting scheduler stop: cfg_a
INFO | [Restart] Stopping the workers, waiting up to 600.0s for: ['cfg_a']
INFO | [WorkerManager] Worker stopped: cfg_a, exitcode=0
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
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 1 configs, 0 actions, updated_all_at={at(4, 0):.0f})
INFO | [Restart] Daily action covered by the full update of this process
INFO | [Restart] Handover of the "daily" flow (daily): 1 configs to resume
INFO | [Restart] Flow "daily" finished
""")
            assert backend.restart.calls == 1
            assert backend.daily.is_due() is False
            assert read_record(backend).configs == ['cfg_a']
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_daily_maintenance_of_a_disabled_backend_is_a_graceful_restart(make_backend, deploy_config, clock):
    """
    Deploy.Update.AutoUpdate = false: the daily maintenance checks and applies
    nothing (its other purpose, the periodic restart, remains)
    """
    deploy_config.auto_update = False
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        with logger.mock_capture_writer() as capture:
            clock.set(at(4, 0))
            nursery.start_soon(backend.daily.run, backend.manager)
            await wait_log(capture, 'Flow "daily" finished')
            await backend.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Schedule] Daily maintenance at 03:50
INFO | [Restart] Flow "daily" accepted
INFO | [Schedule] Daily maintenance started by the schedule ("daily" flow)
INFO | [WorkerManager] Requesting scheduler stop: cfg_a
INFO | [Restart] Stopping the workers, waiting up to 600.0s for: ['cfg_a']
INFO | [WorkerManager] Worker stopped: cfg_a, exitcode=0
INFO | [Update] The mod updates are disabled, the full update is empty
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 1 configs, 0 actions, updated_all_at=0)
INFO | [Restart] Handover of the "daily" flow (daily): 1 configs to resume
INFO | [Restart] Flow "daily" finished
""")
            assert backend.restart.calls == 1
            # no full update ran of this process: the new one starts after the
            # crossing, only a failed handover would keep the debt
            assert backend.daily.is_due() is True
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_crossing_is_served_by_the_update_flow_in_flight(make_backend, clock):
    """
    The clock crosses the target while a user update runs: the flow is
    upgraded into the daily maintenance -- it adds the full update and its
    handover covers the crossing (one full update, one restart)
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestScheduler') == ''
        mod_root = backend.project.mod_root('WorkerTestScheduler')
        with logger.mock_capture_writer() as capture:
            clock.set(at(4, 0))
            flow = backend.manager.update_apply()
            await backend.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Restart] Flow "update" accepted
INFO | [CurrentVersion] v1
INFO | [LatestVersion] v2
INFO | Update available: v1 -> v2
INFO | [WorkerManager] Requesting scheduler stop: cfg_a
INFO | [Restart] Stopping the workers, waiting up to 600.0s for: ['cfg_a']
INFO | [WorkerManager] Worker stopped: cfg_a, exitcode=0
INFO | [Update] Updating mod "WorkerTestScheduler"
INFO | [CurrentVersion] v1
INFO | [LatestVersion] v2
INFO | Update available: v1 -> v2
INFO | Updating files to "{mod_root}"
INFO | Replacing files to "{mod_root}"
INFO | Update done
INFO | [Update] Applied the update of "WorkerTestScheduler"
INFO | [Restart] The "update" flow is upgraded into the daily maintenance (the crossing of the daily target is not served yet)
INFO | [Update] Updating mod "WorkerTestScheduler"
INFO | [CurrentVersion] v2
INFO | [LatestVersion] v2
INFO | Already up to date: v2
INFO | Resetting files to "{mod_root}"
INFO | Reset done
INFO | [Update] Full update finished, changed mods: []
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 1 configs, 0 actions, updated_all_at={at(4, 0):.0f})
INFO | [Restart] Daily action covered by the full update of this process
INFO | [Restart] Handover of the "update" flow (update): 1 configs to resume
INFO | [Restart] Flow "update" finished
""")
            assert flow.upgraded is True
            assert backend.daily.is_due() is False
            assert backend.manager.frozen is True
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_crossing_is_skipped_while_the_backend_starts_up(make_backend, clock):
    """
    The clock crosses the target while the startup flow runs: the process just
    started (its young workers are the restart half of the daily action), the
    crossing is marked served and is not claimed later
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    backend.servers.publish_update()
    gate = trio.Event()
    backend.servers.hold('update_v1.pack', gate)
    mod_root = backend.project.mod_root('WorkerTestScheduler')
    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(backend.manager.run)
            await backend.servers.hold_entered.wait()
            clock.set(at(4, 0))
            nursery.start_soon(backend.daily.run, backend.manager)
            await wait_log(capture, 'Daily action skipped')
            gate.set()
            await backend.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Update] Updating mod "WorkerTestScheduler"
INFO | [CurrentVersion] v1
INFO | [LatestVersion] v2
INFO | Update available: v1 -> v2
INFO | [Schedule] Daily maintenance at 03:50
INFO | [Restart] Daily action skipped: the backend is starting up
INFO | Updating files to "{mod_root}"
INFO | Replacing files to "{mod_root}"
INFO | Update done
INFO | [Update] Applied the update of "WorkerTestScheduler"
INFO | [Update] Full update finished, changed mods: ['WorkerTestScheduler']
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 0 configs, 0 actions, updated_all_at={at(4, 0):.0f})
INFO | [Restart] Handover of the "startup" flow (startup convergence): 0 configs to resume
INFO | [Restart] Flow "startup" finished
""")
            assert backend.daily.is_due() is False
            assert backend.restart.calls == 1
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_failed_daily_handover_wraps_up_and_keeps_running(make_backend, clock):
    """
    The supervisor restart fails: the publication is withdrawn, the applied
    mod is marked restart-required and the wrap-up resumes the worker -- the
    backend keeps running. The crossing was marked served before the restart
    was ordered (the check and the update ran)
    """
    backend = make_backend(restart_error=PermissionError('the supervisor pipe is closed'))
    await backend.install('WorkerTestScheduler')
    mod_root = backend.project.mod_root('WorkerTestScheduler')
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        with logger.mock_capture_writer() as capture:
            clock.set(at(4, 0))
            nursery.start_soon(backend.daily.run, backend.manager)
            await wait_log(capture, 'wraps up')
            await backend.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Schedule] Daily maintenance at 03:50
INFO | [Restart] Flow "daily" accepted
INFO | [Schedule] Daily maintenance started by the schedule ("daily" flow)
INFO | [WorkerManager] Requesting scheduler stop: cfg_a
INFO | [Restart] Stopping the workers, waiting up to 600.0s for: ['cfg_a']
INFO | [WorkerManager] Worker stopped: cfg_a, exitcode=0
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
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 1 configs, 0 actions, updated_all_at={at(4, 0):.0f})
INFO | [Restart] Daily action covered by the full update of this process
INFO | [Restart] Handover of the "daily" flow (daily): 1 configs to resume
ERROR | [Restart] Handover failed, the backend keeps running: the supervisor pipe is closed
INFO | [Restart] Resume credential retracted from the supervisor
INFO | [Restart] Resume file removed by the withdraw: {backend.resume_file}
ERROR | [Restart] The daily maintenance could not hand the backend over: Cannot restart the backend: the supervisor pipe is closed
WARNING | [Update] The update of "WorkerTestScheduler" needs a restart to be applied
INFO | [Restart] The "daily" flow wraps up: releasing the queued workers
INFO | [Restart] Flow "daily" finished
INFO | [Restart] Resume queue: 1 configs, starting them one by one
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
""")
            assert backend.manager.frozen is False
            assert backend.daily.is_due() is False
            assert backend.update_check.state_of('WorkerTestScheduler') == 'restart-required'
            nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_schedule_does_nothing_without_a_supervisor(make_backend, monkeypatch):
    """
    The backend cannot replace itself (no parent process): the daily restart
    and the mod updates are disabled, the schedule has nothing to do and ends
    """
    monkeypatch.delattr(builtins, '__mpipe_conn__', raising=False)
    backend = make_backend()
    with logger.mock_capture_writer() as capture:
        await backend.daily.run(backend.manager)
        assert_logs(capture, """\
INFO | [Schedule] The backend runs without a supervisor: the daily maintenance is disabled
""")
