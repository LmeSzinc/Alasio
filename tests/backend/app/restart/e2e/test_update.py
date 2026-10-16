"""
Scenario one of the realcase runs: the user clicks update

The real ``FlowUpdate`` over the real backend of the conftest: pause the
checks, pre-check the mods, stop the workers, apply the real deploy jobs and
hand the process over. The branches: an update already applied, a cancel of
each cancellable phase, one broken mirror among healthy mods, a start accepted
while the flow owns the backend, a refused restart, a failed handover.

Every test asserts the complete log of its run, see the conftest docstring.
"""
import pytest
import trio

from alasio.backend.app.restart.restart_base import FlowRefused
from alasio.logger import logger

from .conftest import FULL_V2, RESUME_INTERVAL, assert_logs, at, read_record, wait_until


@pytest.mark.trio
async def test_applies_the_update_and_hands_the_backend_over(make_backend):
    """
    A new version is published while the backend runs: the manual check
    reports it, the update flow stops the real worker, applies the real update
    pack and hands the process over
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    mod_root = backend.project.mod_root('WorkerTestScheduler')
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestScheduler') == ''
        assert backend.update_check.state_of('WorkerTestScheduler') == 'available'
        with logger.mock_capture_writer() as capture:
            backend.manager.update_apply()
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
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 1 configs, 0 actions, updated_all_at=1791828000)
INFO | [Restart] Handover of the "update" flow (update): 1 configs to resume
INFO | [Restart] Flow "update" finished
""")
        record = read_record(backend)
        assert record.configs == ['cfg_a']
        assert backend.restart.calls == 1
        assert backend.manager.frozen is True
        # the worker is parked for the new process: no process any more
        assert backend.worker_manager.state['cfg_a'].process is None
        nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_pre_check_makes_the_update_a_noop_when_it_was_already_applied(make_backend):
    """
    Another flow applied the update meanwhile: the read-only pre-check finds
    the tree up to date and the flow ends before anything is stopped
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestScheduler') == ''
        # the files are already on disk (another flow applied them)
        await backend.install('WorkerTestScheduler', FULL_V2)
        with logger.mock_capture_writer() as capture:
            backend.manager.update_apply()
            await backend.wait_quiet()
            assert_logs(capture, """\
INFO | [Restart] Flow "update" accepted
INFO | [CurrentVersion] v2
INFO | [LatestVersion] v2
INFO | Already up to date: v2
INFO | [Restart] The update is already applied, nothing to do
INFO | [Restart] The "update" flow wraps up: releasing the queued workers
INFO | [Restart] Flow "update" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
        assert backend.restart.calls == 0
        assert backend.manager.frozen is False
        # the worker was never stopped
        assert await trio.to_thread.run_sync(backend.worker_manager.worker_wait_running, 'cfg_a', 2.0)
        nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_cancel_during_the_pre_check_stops_nothing(make_backend):
    """
    The cancel lands in the read-only pre-check (the slow mirror holds the
    request): no worker is stopped, nothing is written, no handover
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    gate = trio.Event()
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestScheduler') == ''
        # the pre-check of the flow hangs on the mirror
        backend.servers.hold('/latest.pack', gate)
        with logger.mock_capture_writer() as capture:
            flow = backend.manager.update_apply()
            await backend.servers.hold_entered.wait()
            assert flow.cancellable is True
            await backend.manager.cancel_apply()
            await backend.wait_quiet()
            assert_logs(capture, """\
INFO | [Restart] Flow "update" accepted
INFO | [CurrentVersion] v1
INFO | [Restart] The "update" flow wraps up: releasing the queued workers
INFO | [Restart] The update flow was cancelled
INFO | [Restart] Flow "update" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
        assert backend.restart.calls == 0
        assert backend.manager.frozen is False
        assert await trio.to_thread.run_sync(backend.worker_manager.worker_wait_running, 'cfg_a', 2.0)
        nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_cancel_during_the_download_resumes_the_worker(make_backend):
    """
    The cancel lands while the update pack is downloading (the slow mirror
    holds the request): nothing was written, the wrap-up re-queues the worker
    it stopped and the resume queue starts it again
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    gate = trio.Event()
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestScheduler') == ''
        # the update pack of the apply hangs on the mirror
        backend.servers.hold('from_v1.pack', gate)
        with logger.mock_capture_writer() as capture:
            flow = backend.manager.update_apply()
            await backend.servers.hold_entered.wait()
            assert flow.cancellable is True
            await backend.manager.cancel_apply()
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
INFO | [Restart] The "update" flow wraps up: releasing the queued workers
INFO | [Restart] The update flow was cancelled
INFO | [Restart] Flow "update" finished
INFO | [Restart] Resume queue: 1 configs, starting them with a {RESUME_INTERVAL}s interval
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
""")
        assert backend.restart.calls == 0
        assert backend.manager.frozen is False
        assert backend.worker_manager.state['cfg_a'].process is not None
        nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_cancel_during_the_stop_wait_ends_the_flow(make_backend):
    """
    The cancel lands while the flow waits for a worker that ignores the
    graceful stop: the abort ends the wait, the worker never stopped for the
    flow so nothing is queued for the resume, and the flow wraps up
    """
    backend = make_backend(mods={'WorkerTestInfinite': 'http://update.mock'})
    await backend.install('WorkerTestInfinite')
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestInfinite') == ''
        with logger.mock_capture_writer() as capture:
            flow = backend.manager.update_apply()
            # the wait is in flight and the worker keeps running (the stop is
            # a request, not a kill)
            await wait_until(lambda: backend.worker_manager.state['cfg_a'].state == 'scheduler-stopping')
            assert flow.cancellable is True
            await backend.manager.cancel_apply()
            await backend.wait_quiet()
            assert_logs(capture, """\
INFO | [Restart] Flow "update" accepted
INFO | [CurrentVersion] v1
INFO | [LatestVersion] v2
INFO | Update available: v1 -> v2
INFO | [WorkerManager] Requesting scheduler stop: cfg_a
INFO | [Restart] Stopping the workers, waiting up to 600.0s for: ['cfg_a']
INFO | [Restart] The "update" flow wraps up: releasing the queued workers
INFO | [Restart] The update flow was cancelled
INFO | [Restart] Flow "update" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
        assert backend.restart.calls == 0
        assert backend.manager.frozen is False
        # the worker ignored the graceful stop: it keeps running, it was never
        # stopped for the flow and is not queued for a resume
        assert backend.worker_manager.state['cfg_a'].state == 'scheduler-stopping'
        assert backend.worker_manager.state['cfg_a'].process is not None
        nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_broken_mirror_is_isolated_and_the_rest_are_applied(make_backend):
    """
    One mirror dies after the check: its mod is reported on its own card (the
    apply fails), the other mod is still applied and the flow still hands the
    backend over
    """
    backend = make_backend(
        mods={'WorkerTestScheduler': 'http://update.mock', 'WorkerTestSleep': 'http://dying.mock'},
        configs={'cfg_a': 'WorkerTestScheduler'},
    )
    await backend.install('WorkerTestScheduler')
    await backend.install('WorkerTestSleep')
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestScheduler') == ''
        assert await backend.update_check.update_check('WorkerTestSleep') == ''
        # the second mirror dies between the check and the apply
        backend.servers.break_host('dying.mock')
        mod_root = backend.project.mod_root('WorkerTestScheduler')
        with logger.mock_capture_writer() as capture:
            backend.manager.update_apply()
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
INFO | [Update] Updating mod "WorkerTestSleep"
INFO | [CurrentVersion] v1
WARNING | Request to "http://dying.mock/latest.pack" failed (attempt 1/3): HTTPStatusError: Client error '404 Not Found' for url 'http://dying.mock/latest.pack'
ERROR | [Update] Failed to update "WorkerTestSleep": Client error '404 Not Found' for url 'http://dying.mock/latest.pack'
ERROR | HTTPStatusError: Client error '404 Not Found' for url 'http://dying.mock/latest.pack'
<traceback>
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 1 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "update" flow (update): 1 configs to resume
INFO | [Restart] Flow "update" finished
""")
        assert backend.update_check.state_of('WorkerTestScheduler') == 'uptodate'
        assert backend.update_check.state_of('WorkerTestSleep') == 'error'
        assert read_record(backend).configs == ['cfg_a']
        nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_start_accepted_during_the_flow_joins_the_resume_file(make_backend):
    """
    The user starts a second config while the flow owns the backend: the real
    worker manager accepts it as a queued resume, the stop collects it and the
    resume file carries it to the new process
    """
    backend = make_backend(configs={'cfg_a': 'WorkerTestScheduler', 'cfg_b': 'WorkerTestScheduler'})
    await backend.install('WorkerTestScheduler')
    gate = trio.Event()
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestScheduler') == ''
        # the pre-check of the flow hangs: the flow owns the backend
        backend.servers.hold('/latest.pack', gate)
        mod_root = backend.project.mod_root('WorkerTestScheduler')
        with logger.mock_capture_writer() as capture:
            backend.manager.update_apply()
            await backend.servers.hold_entered.wait()
            success, msg = await trio.to_thread.run_sync(
                backend.worker_manager.worker_start, backend.mods['WorkerTestScheduler'], 'cfg_b')
            assert success, msg
            gate.set()
            await backend.wait_quiet()
            assert_logs(capture, f"""\
INFO | [Restart] Flow "update" accepted
INFO | [CurrentVersion] v1
INFO | [WorkerManager] Start queued by the maintenance flow: cfg_b
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
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 2 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "update" flow (update): 2 configs to resume
INFO | [Restart] Flow "update" finished
""")
        assert read_record(backend).configs == ['cfg_a', 'cfg_b']
        nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_the_user_restart_is_refused_while_the_update_runs(make_backend):
    """
    The restart entry answers with the refusal of the flow in flight; the
    update runs to its handover untouched
    """
    backend = make_backend()
    await backend.install('WorkerTestScheduler')
    gate = trio.Event()
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestScheduler') == ''
        backend.servers.hold('from_v1.pack', gate)
        mod_root = backend.project.mod_root('WorkerTestScheduler')
        with logger.mock_capture_writer() as capture:
            backend.manager.update_apply()
            await backend.servers.hold_entered.wait()
            with pytest.raises(FlowRefused) as e:
                backend.manager.restart()
            assert str(e.value) == ('An update is in progress; the backend restarts by itself '
                                    'when it finishes')
            assert backend.manager.flow_name == 'update'
            gate.set()
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
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 1 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "update" flow (update): 1 configs to resume
INFO | [Restart] Flow "update" finished
""")
        assert backend.restart.calls == 1
        nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_a_failed_handover_wraps_up_and_marks_the_written_mod(make_backend):
    """
    The supervisor restart fails (a dead pipe): the publication is withdrawn,
    the applied mod is marked restart-required and the wrap-up resumes the
    worker it stopped
    """
    backend = make_backend(restart_error=PermissionError('the supervisor pipe is closed'))
    await backend.install('WorkerTestScheduler')
    async with trio.open_nursery() as nursery:
        await backend.start(nursery)
        await backend.start_worker('cfg_a')
        backend.servers.publish_update()
        assert await backend.update_check.update_check('WorkerTestScheduler') == ''
        mod_root = backend.project.mod_root('WorkerTestScheduler')
        with logger.mock_capture_writer() as capture:
            backend.manager.update_apply()
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
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 1 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "update" flow (update): 1 configs to resume
ERROR | [Restart] Handover failed, the backend keeps running: the supervisor pipe is closed
INFO | [Restart] Resume credential retracted from the supervisor
INFO | [Restart] Resume file removed by the withdraw: {backend.resume_file}
ERROR | [Restart] The update could not hand the backend over: Cannot restart the backend: the supervisor pipe is closed
WARNING | [Update] The update of "WorkerTestScheduler" needs a restart to be applied
INFO | [Restart] The "update" flow wraps up: releasing the queued workers
INFO | [Restart] Flow "update" finished
INFO | [Restart] Resume queue: 1 configs, starting them with a {RESUME_INTERVAL}s interval
INFO | [WorkerManager] Resuming worker: cfg_a
INFO | [Restart] Worker resumed: cfg_a
""")
        assert backend.restart.calls == 1
        assert backend.update_check.state_of('WorkerTestScheduler') == 'restart-required'
        assert backend.worker_manager.state['cfg_a'].process is not None
        nursery.cancel_scope.cancel()
