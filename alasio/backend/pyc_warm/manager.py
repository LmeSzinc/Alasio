"""
The bytecode warmup of the running release, as a process the backend owns.

The warmup compiles the pyc files that the first import after an update
would otherwise compile, see alasio.deploy.pyc_warm: the scan is IO bound
and the compilation is CPU bound, so it has no reason to run on the loop
thread, and it must not outlive the backend it serves.

The process is a WorkerManager child rather than a hand rolled subprocess:
the manager already owns the three ways a child has to die with its parent
(explicit close(), daemon, and the pipe EOF a child sees when the backend
is killed outright), see alasio.backend.worker.manager. The warmup only
answers the process it wants, everything else -- the pipe, the "starting"
window, the recv thread, the terminate/kill escalation -- is inherited.

The report is logged and dropped: the warmup describes one run, and the
lines of the backend are where a user reads why a release started slowly.
"""
import msgspec
from msgspec.msgpack import decode

from alasio.backend.pyc_warm.entry import pyc_warm_entry
from alasio.backend.worker.manager import WorkerManager, WorkerState
from alasio.ext import env
from alasio.logger import logger

# Name of the single entry of the manager: the warmup runs once per backend
# process, it is not a per-config resource like a worker
WARMUP_NAME = 'pyc_warm'


class PycWarmManager(WorkerManager):
    """
    The warmup subprocess of this backend, in the lifecycle of a worker.

    Only start() and the inherited close() are used: the worker entry points
    (worker_start / worker_kill / the restart flow) are not, a warmup has no
    config to schedule and no state to resume. They are inherited rather than
    removed because the lifecycle that matters -- the pipe, the recv thread,
    the disconnect handler and close() -- is exactly the worker one.

    The report of a run goes to the log and is not kept: it describes a run
    that happened, and the state of the cache is read from the disk by the
    next run, never from a value of this object.
    """

    PROCESS_PREFIX = 'Warmup'

    def start(self):
        """
        Start the warmup subprocess, if it is not already running.

        Best effort: a spawn that fails is a warning, the release runs the
        way it ran before the warmup existed.

        Returns:
            bool: True when the warmup was started
        """
        with self._lock:
            state = self.state.get(WARMUP_NAME)
            if state is None:
                state = WorkerState(mod=WARMUP_NAME, config=WARMUP_NAME, state='idle')
                self.state[WARMUP_NAME] = state
            if state.state not in ('idle', 'error'):
                # Already running, or the entry of a run that is winding down
                return False
            child_conn = self._mark_starting_locked(state)

        try:
            success, reason = self._worker_start_process(
                state, WARMUP_NAME, WARMUP_NAME, child_conn,
                project_root=str(env.PROJECT_ROOT),
            )
        except Exception as e:
            # _worker_start_process already returned the entry to a stopped
            # state: the warmup is an optimization, a spawn that fails is a
            # warning and never an error of the backend
            logger.warning(f'[PycWarm] Failed to start: {e}')
            return False
        return success

    def _spawn_process(self, mod, config, child_conn, project_root='', mod_root='', path_main=''):
        """
        Create the warmup process of this backend, without starting it.

        The entry is declared in a module of stdlib imports only, so the
        backend keeps a light reference to it, see pyc_warm/entry.py.

        Args:
            mod (str): Name of the entry, the name of the warmup run
            config (str): Name of the entry
            child_conn (Connection): The child end of the pipe
            project_root (str): Project root, the tree the mods declare in
            mod_root (str): Unused, the warmup scans every mod of the root
            path_main (str): Unused, the warmup runs no mod entry file

        Returns:
            multiprocessing.Process: The process, not started yet
        """
        args = (mod, config, child_conn, project_root, mod_root, path_main)
        return self._ctx.Process(
            target=pyc_warm_entry,
            args=args,
            name=WARMUP_NAME,
            daemon=True,
        )

    @staticmethod
    def _recv_thread_name(config):
        """
        The thread of the warmup is named after the run, not after a config.

        Args:
            config (str): Unused, the warmup runs once

        Returns:
            str: Name of the thread
        """
        return WARMUP_NAME

    def _handle_config_event(self, data, worker):
        """
        Log the report the warmup sent before it exited.

        Args:
            data (bytes): The msgpack report of the warmup
            worker (WorkerState): The entry the data came from
        """
        try:
            report = decode(data)
        except msgspec.DecodeError as e:
            logger.warning(f'[PycWarm] Unreadable report: {e}')
            return
        log_report(report)


def log_report(report):
    """
    Log what one warmup run did.

    The summary line is the answer to "was the release warm", and a unit that
    could not compile is a line of its own: the release runs either way, the
    lines are the only place the reason survives.

    Args:
        report (dict): What the warmup answered, see
            alasio.deploy.pyc_warm.runner.Report.to_dict()
    """
    error = report.get('error')
    if error is not None:
        # The run raised before it could describe anything, e.g. the scope of
        # the release could not be read
        logger.warning(f'[PycWarm] Warmup failed: {error}')
        return

    results = report.get('results') or []
    missing = [result for result in results if not result.get('sources')]
    logger.info(
        f'[PycWarm] {report.get("stale", 0)} stale, {report.get("compiled", 0)} compiled, '
        f'{report.get("failed", 0)} failed in {report.get("seconds", 0.0):.2f}s, '
        f'{len(results)} units, {len(missing)} found nothing'
    )
    for result in results:
        if not result.get('failed'):
            continue
        sample = (result.get('errors') or [''])[0]
        logger.warning(
            f'[PycWarm] {result["tier"]}/{result["target"]}: '
            f'{result["failed"]} of {result["stale"]} failed: {sample}'
        )


PYC_WARM_MANAGER = PycWarmManager()
