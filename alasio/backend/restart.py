"""
Graceful backend restart orchestration

The old backend stops every worker gracefully (scheduler-stopping, waiting for
the current task to finish, escalating to a kill after a timeout), writes the
resume intent to a file and asks the supervisor to restart the process. The new
backend consumes the file -- only with the one-shot credential handed over
through the supervisor -- and starts the recorded workers again.

File protocol (see doc/2026-09-13_graceful-backend-restart.md):
- path: <PROJECT_ROOT>/log/resume/resume-{token}.json, token = random hex;
- content: the payload itself, {"ts", "owner", "configs", "actions"}; written
  once, after the workers stopped (never during the wait);
- the folder may hold the files of other backends as well: a transaction only
  ever touches the file named by its own token (write / read-and-delete /
  defensive cancel), it never deletes or rewrites a foreign file; leftovers
  of dead sessions are removed by resume_cleanup() (age based) only;
- credential: f'{token}-{checksum}' with checksum = HMAC-SHA256(token, payload);
  the checksum is NOT stored in the file, so a file whose credential was never
  announced is never consumed (the read requires the token from the
  credential);
- the file is read at most once and always deleted right after the read.

Credential handover (same session): the old backend announces the credential
with command:resume:<credential>; the supervisor stores it and injects it into
the next spawned backend as ALASIO_RESUME_TOKEN, then clears it once the new
backend announced startup completion (command:started).
"""
import hashlib
import hmac
import os
import secrets
import time
from typing import List, Optional

import msgspec
import trio

from alasio.backend.lifespan import SHUTDOWN_EVENT, lifespan_restart
from alasio.backend.mpipe.mpipe_backend import mpipe_backend
from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER
from alasio.backend.topic.restart import RestartSource
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, atomic_remove, atomic_write
from alasio.logger import logger

# Seconds to wait for the workers to stop gracefully before killing the
# remaining ones (R3: maximum wait of the graceful stop)
GRACEFUL_STOP_TIMEOUT = 600.0
# Interval between two worker starts of the auto-resume queue (measured from
# the previous worker_resume return; the queue exists to avoid spawning dozens
# of processes and device connections in the same instant)
WORKER_START_INTERVAL = 1.0
# Age of a resume file that counts as a stale leftover: nothing stale can be
# consumed (a read requires the one-shot credential), this only keeps the disk
# clean after a session was killed before its transaction finished
RESUME_CLEANUP_AGE = 3 * 24 * 3600.0
# Seconds to wait for the config scan to expose the recorded configs (a config
# created right before the restart may not be visible yet)
RESUME_CONFIG_WAIT = 60.0
# Environment variable the supervisor injects into the new backend
RESUME_TOKEN_ENV = 'ALASIO_RESUME_TOKEN'
# Resume file name prefix / suffix (resume-{token}.json)
RESUME_FILE_PREFIX = 'resume-'
RESUME_FILE_SUFFIX = '.json'
# Owner of a resume file: the graceful restart writes 'restart', the in-app
# update flow writes 'update' (its file lifetime belongs to the update
# transaction, the restart cancel path must not delete it)
OWNER_RESTART = 'restart'


class ResumeRecord(msgspec.Struct):
    """
    Resume file payload: the configs to auto-resume after a graceful backend
    restart, plus optional actions the new backend runs before the resume.
    """
    # unix timestamp of the write
    ts: float
    # transaction owner: 'restart' or 'update'
    owner: str
    # config names to auto-resume
    configs: List[str]
    # built-in action tags (no shell) executed before the resume
    actions: List[str]


class RestartHooks:
    """
    Optional hooks of a restart transaction (used by the in-app update flow,
    which reuses the whole orchestration)

    Attributes:
        on_all_stopped (callable): Async callback run after every worker
            stopped and before the backend shutdown. The backend process is
            still alive and its python modules are loaded: replacing the files
            on disk is safe here (nothing holds the files open) and the
            supervisor restarts the backend with the new code afterwards. A
            raised error cancels the restart.
        actions (list[str]): Action tags carried by the resume file and
            executed by the new backend before the auto-resume (e.g. cleaning
            stale bytecode after a code update)
    """

    def __init__(self, on_all_stopped=None, actions=None):
        # async callback, or None
        self.on_all_stopped = on_all_stopped
        # action tags, a list of str
        self.actions = list(actions) if actions else []


class GracefulRestart:
    """
    Runtime state of the restart orchestration (module singleton)

    One instance is one backend process: besides the runtime state it owns the
    resume file IO (path, credential, write / read / cleanup) of its own
    transaction.

    Attributes:
        manager (WorkerManager): Worker manager driving the orchestration,
            injectable for tests (defaults to BACKEND_WORKER_MANAGER)
        running (bool): True while this backend owns a restart transaction.
            Set synchronously by the rpc handler (re-entry guard), cleared by
            cancel_graceful_restart(); kept on the success path, the gate stays
            until the process exits
        scope (trio.CancelScope): Orchestration task scope, cancelled by
            cancel_graceful_restart()
        resume_scope (trio.CancelScope): Resume task scope of the new backend
        resume_file (PathStr): Resume file written by this transaction (for
            the defensive cancel cleanup), None when nothing was written yet
        resume_owner (str): Owner of the written resume file
    """

    def __init__(self):
        self.manager = None
        self.running = False
        self.scope = None
        self.resume_scope = None
        self.resume_file: "Optional[PathStr]" = None
        self.resume_owner = ''
        # resume_folder is a cached_property: drop the cache on a re-init
        # (reset(), a caller repointing PROJECT_ROOT) so the current
        # PROJECT_ROOT is read again
        cached_property.pop(self, 'resume_folder')

    def reset(self):
        """
        Reset to the initial state (test helper)
        """
        self.__init__()

    # =========================================================================
    # Resume file: path, credential, read / write / cleanup
    # =========================================================================

    @cached_property
    def resume_folder(self) -> PathStr:
        """
        Folder holding the resume files (shared by every backend of a project)

        Cached: PROJECT_ROOT is bound once per process at the backend startup.

        Returns:
            PathStr: <PROJECT_ROOT>/log/resume
        """
        return env.PROJECT_ROOT.joinpath('log/resume')

    def resume_file_of(self, token: str) -> PathStr:
        """
        Path of the resume file of one token

        The `resume_file` attribute holds the file THIS transaction wrote;
        this method resolves where the file of any token lives (token is the
        isolation, the file name carries it).

        Args:
            token (str): One-shot random token

        Returns:
            PathStr: <PROJECT_ROOT>/log/resume/resume-{token}.json
        """
        return self.resume_folder.joinpath(f'{RESUME_FILE_PREFIX}{token}{RESUME_FILE_SUFFIX}')

    def resume_checksum(self, token: str, payload: bytes) -> str:
        """
        HMAC-SHA256 of the payload under the token (the credential's judge)

        Args:
            token (str): One-shot random token
            payload (bytes): Exact bytes written to / read from the resume file

        Returns:
            str: Hex digest
        """
        return hmac.new(token.encode(), payload, hashlib.sha256).hexdigest()

    def iter_resume_files(self) -> "List[PathStr]":
        """
        Existing resume files (empty when the folder or the file list is missing)

        Returns:
            List[PathStr]: Full paths of the resume-*.json files
        """
        folder = self.resume_folder
        try:
            entries = list(folder.iter_entry())
        except (FileNotFoundError, NotADirectoryError, OSError):
            return []
        files = []
        for entry in entries:
            try:
                name = entry.name
                if not (name.startswith(RESUME_FILE_PREFIX) and name.endswith(RESUME_FILE_SUFFIX)):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            files.append(folder.joinpath(name))
        return files

    def write_resume(self, resume_list, owner=OWNER_RESTART, actions=None) -> str:
        """
        Write the resume intent and mint its one-shot credential

        Called once per restart transaction, after restart_wait() returned (never
        during the wait): the content is the final resume list, there is no
        intermediate write and no rewrite. Only the file of this transaction is
        written: other resume files in the folder (another backend, a session that
        died before its transaction finished) are left untouched -- they can never
        be consumed without their own credential, and the stale ones are removed
        by resume_cleanup().

        Args:
            resume_list (list[str]): Configs to auto-resume after the restart
            owner (str): Transaction owner, OWNER_RESTART ('restart') or 'update'
            actions (list[str]): Optional action tags the new backend runs before
                the resume

        Returns:
            str: Credential string f'{token}-{checksum}' to announce to the
                supervisor
        """
        token = secrets.token_hex(16)
        record = ResumeRecord(
            ts=time.time(),
            owner=owner,
            configs=list(resume_list),
            actions=list(actions) if actions else [],
        )
        payload = msgspec.json.encode(record)
        checksum = self.resume_checksum(token, payload)
        file = self.resume_file_of(token)
        atomic_write(file, payload)
        self.resume_file = file
        self.resume_owner = owner
        logger.info(f'[Restart] Resume file written: {file} '
                    f'(owner={owner}, {len(record.configs)} configs, {len(record.actions)} actions)')
        return f'{token}-{checksum}'

    def read_resume(self) -> "Optional[ResumeRecord]":
        """
        Consume the resume file of this backend (new backend startup)

        The credential comes from the supervisor (ALASIO_RESUME_TOKEN). Without a
        credential nothing is read, so a file left behind by a killed session can
        never be consumed. With a credential the file named by its token is read
        and deleted immediately -- a file never survives a read, whatever the
        verification result -- then the payload is verified against the checksum.

        Returns:
            ResumeRecord | None: The verified record, or None (no credential, no
                file, checksum mismatch, malformed payload)
        """
        credential = os.environ.get(RESUME_TOKEN_ENV, '')
        if not credential:
            # logger.info('[Restart] Resume file not read: no env credential')
            return None
        token, sep, checksum = credential.partition('-')
        if not (sep and token and checksum):
            logger.warning('[Restart] Resume file not read: malformed credential')
            return None
        file = self.resume_file_of(token)
        try:
            payload = atomic_read_bytes(file)
        except FileNotFoundError:
            logger.info(f'[Restart] Resume file not found: {file}')
            return None
        except OSError as e:
            logger.warning(f'[Restart] Resume file not readable: {file}: {e}')
            return None
        # read once: the file never survives a read, valid or not
        atomic_remove(file)
        actual = self.resume_checksum(token, payload)
        if not hmac.compare_digest(actual, checksum):
            logger.warning('[Restart] Resume file dropped: checksum mismatch (tampered or corrupted)')
            return None
        try:
            record = msgspec.json.decode(payload, type=ResumeRecord)
        except msgspec.ValidationError as e:
            logger.warning(f'[Restart] Resume file dropped: invalid payload: {e}')
            return None
        return record

    def resume_cleanup(self):
        """
        Remove stale resume files (lifespan startup, best effort)

        Deletes resume files older than RESUME_CLEANUP_AGE. Nothing stale can be
        consumed anyway (a read requires the one-shot credential); this only keeps
        the disk clean when a process was killed before its transaction finished
        (the normal path is deleted by the read itself). Never raises: a cleanup
        problem must not block the backend startup.
        """
        try:
            files = self.iter_resume_files()
        except Exception as e:
            logger.warning(f'[Restart] Resume cleanup failed: {e}')
            return
        now = time.time()
        removed = 0
        kept = 0
        for file in files:
            try:
                st = file.stat()
            except (FileNotFoundError, OSError):
                continue
            if now - st.st_mtime > RESUME_CLEANUP_AGE:
                try:
                    if atomic_remove(file):
                        removed += 1
                except OSError as e:
                    logger.warning(f'[Restart] Failed to remove stale resume file {file}: {e}')
                continue
            kept += 1
        if removed or kept:
            logger.info(f'[Restart] Resume cleanup: removed {removed} stale files, kept {kept}')

    def announce_resume_token(self, credential: str):
        """
        Announce the resume credential to the supervisor

        Sends b'command:resume:<credential>'; the supervisor stores it and injects
        it into the next spawned backend (ALASIO_RESUME_TOKEN). Without a supervisor
        the message is silently dropped (send default), so a file written without a
        supervisor is never consumed: the credential cannot be handed over.

        Args:
            credential (str): Credential string returned by write_resume()
        """
        # write the file first, announce after: the invariant is "no credential
        # announced => the file is never read", so a crash in between leaves an
        # unconsumable file (cleaned up by the next transaction / the age cleanup)
        mpipe_backend.send(b'command:resume:' + credential.encode())
        logger.info('[Restart] Resume credential announced to the supervisor')


GRACEFUL_RESTART = GracefulRestart()


# =============================================================================
# Restart topic phase
# =============================================================================

def push_restart_phase(phase: str):
    """
    Push the restart phase to the Restart topic (any thread safety)

    Args:
        phase (str): '' clears the topic, otherwise 'stopping' /
            'shutting-down' / 'resuming' / 'done'
    """
    try:
        RestartSource().on_event(phase)
    except Exception as e:
        # the phase is a progress hint, never let it break the restart
        logger.warning(f'[Restart] Failed to push phase "{phase}": {e}')


# =============================================================================
# Resume actions (carried by the resume file, run before the resume)
# =============================================================================

# Action tag -> runner. No shell: an action is a cleanup the new backend runs
# before the auto-resume (the in-app update flow injects tags through
# write_resume(..., actions=[...]) and registers their runners here).
# No builtin action is registered: replacing the .py files of an updated
# package needs no bytecode cleanup, the interpreter drops a stale .pyc on its
# own (the .pyc header records the source mtime and size, a changed source is
# recompiled).
RESUME_ACTIONS = {}


def run_resume_actions(actions):
    """
    Run the actions recorded in the resume file (best effort, no shell)

    Args:
        actions (list[str]): Action tags
    """
    for action in actions:
        runner = RESUME_ACTIONS.get(action, None)
        if runner is None:
            logger.warning(f'[Restart] Unknown resume action ignored: {action}')
            continue
        logger.info(f'[Restart] Running resume action: {action}')
        try:
            runner()
        except Exception as e:
            logger.error(f'[Restart] Resume action failed: {action}: {e}')


# =============================================================================
# Orchestration (old backend)
# =============================================================================

def _cancel_scope_from_any_thread(scope):
    """
    Cancel a trio cancel scope from any thread (cancel scopes are not thread
    safe, the cancellation must run on the trio thread)

    Args:
        scope (trio.CancelScope): Scope to cancel
    """
    if scope is None:
        return
    token = GLOBAL_CONTEXT.trio_token
    if token is not None:
        try:
            token.run_sync_soon(scope.cancel)
            return
        except trio.RunFinishedError:
            # loop already gone, the task is gone with it
            pass
    scope.cancel()


async def run_graceful_restart(manager=None, hooks=None):
    """
    Graceful restart orchestration (old backend)

    Runs as a trio background task (the rpc returns immediately): the graceful
    stop can last up to GRACEFUL_STOP_TIMEOUT and the frontend keeps watching
    the worker states through the Worker topic while it waits.

    The resume file is written once the wait is over -- never during it -- and
    the credential is announced to the supervisor right after. On the success
    path the manager gate is kept (until the process exits): a worker started
    in the restart window would be killed at exit and never resumed.

    Args:
        manager (WorkerManager): Manager to drive, defaults to
            BACKEND_WORKER_MANAGER (injectable for tests)
        hooks (RestartHooks): Optional hooks of the in-app update flow
    """
    if manager is None:
        manager = BACKEND_WORKER_MANAGER
    GRACEFUL_RESTART.manager = manager
    with trio.CancelScope() as scope:
        GRACEFUL_RESTART.scope = scope
        try:
            try:
                waiting = manager.restart_begin()
            except Exception as e:
                # already restarting (the rpc re-entry guard makes this
                # unreachable) or nothing to begin: release the rpc flag
                logger.error(f'[Restart] Graceful restart cannot begin: {e}')
                GRACEFUL_RESTART.running = False
                return

            push_restart_phase('stopping')
            # the returned list is the whole waiting set of this restart (the
            # final resume list is the return value of restart_wait later)
            logger.info(
                f'[Restart] Graceful restart requested, '
                f'waiting up to {GRACEFUL_STOP_TIMEOUT:.0f}s for the workers to stop, '
                f'waiting for: {waiting}')

            # 1) block for every worker to stop; the wait lives in the manager
            #    (thread-safe, no trio), the timeout escalation happens inside
            #    restart_wait and the abort event makes it return early
            resume_list = await trio.to_thread.run_sync(manager.restart_wait, GRACEFUL_STOP_TIMEOUT)
            if manager.restart_aborted():
                # cancelled while waiting (force restart / backend stop):
                # never write a resume file and never restart
                logger.info('[Restart] Graceful restart was cancelled')
                return

            # 2) the wait is over: write the resume intent once and announce
            #    the credential (announce after the write)
            actions = getattr(hooks, 'actions', None)
            if resume_list or actions:
                credential = GRACEFUL_RESTART.write_resume(resume_list, actions=actions)
                GRACEFUL_RESTART.announce_resume_token(credential)
            else:
                logger.info('[Restart] No worker to resume and no action to run, '
                            'restarting backend directly')

            # 3) replacement window of the in-app update flow: every worker
            #    stopped, the backend still runs (a failure here cancels below)
            if hooks is not None and hooks.on_all_stopped is not None:
                await hooks.on_all_stopped()

            # 4) all workers stopped: enter the existing backend restart step.
            #    The success path does NOT release the gate: it stays until the
            #    process exits, so no worker is started (and lost) in between
            push_restart_phase('shutting-down')
            logger.info(f'[Restart] All workers stopped, restarting backend, resume list: {resume_list}')
            await lifespan_restart()
        except trio.Cancelled:
            # the cancel path (cancel_graceful_restart) owns the cleanup
            raise
        except Exception as e:
            cancel_graceful_restart(f'graceful restart failed: {e}')
            logger.error(f'[Restart] Graceful restart failed: {e}')
            logger.exception(e)
            raise
        finally:
            if GRACEFUL_RESTART.scope is scope:
                GRACEFUL_RESTART.scope = None


# =============================================================================
# Cancel
# =============================================================================

def cancel_graceful_restart(reason: str = ''):
    """
    Cancel the graceful restart in progress (idempotent, any thread)

    Called by the force_restart rpc and the backend shutdown paths (stdin stop
    / supervisor pipe EOF). Cancels the orchestration task (old backend) and
    the resume task (new backend), releases the manager gate, removes the
    resume file this transaction wrote (owner='restart' only: an update-owned
    file belongs to the update transaction) and clears the Restart topic.

    Args:
        reason (str): Log message
    """
    # 1) cancel the orchestration / resume task: they must not continue with
    #    the restart or the resume queue (the scheduled cancel runs on the
    #    trio thread, the waiting thread stops through the manager abort event)
    _cancel_scope_from_any_thread(GRACEFUL_RESTART.scope)
    _cancel_scope_from_any_thread(GRACEFUL_RESTART.resume_scope)
    GRACEFUL_RESTART.running = False
    # 2) release the manager gate, clear the marks, drop the parked entries
    manager = GRACEFUL_RESTART.manager
    if manager is not None:
        try:
            manager.restart_cancel()
        except Exception as e:
            logger.warning(f'[Restart] Failed to cancel the manager state: {e}')
    # 3) defensive cleanup of the resume file written by this transaction: the
    #    write happens after the wait, when the restart is no longer
    #    cancellable, so this only covers a failure in between
    file = GRACEFUL_RESTART.resume_file
    owner = GRACEFUL_RESTART.resume_owner
    GRACEFUL_RESTART.resume_file = None
    GRACEFUL_RESTART.resume_owner = ''
    if file is not None and owner == OWNER_RESTART:
        try:
            if atomic_remove(file):
                logger.info(f'[Restart] Resume file removed by the cancel: {file}')
        except OSError as e:
            logger.warning(f'[Restart] Failed to remove the resume file {file}: {e}')
    # 4) no restart in progress any more
    push_restart_phase('')
    logger.info(f'[Restart] Graceful restart cancelled: {reason}')


# =============================================================================
# Resume (new backend)
# =============================================================================

async def _wait_configs_ready(configs, timeout=None):
    """
    Wait until the config scan exposes the recorded configs

    The lifespan startup does not order the config scan warmup against the
    resume task: a config may not be visible yet when the resume runs. The
    scan cache is refreshed (disk re-read, TTL-throttled) until every recorded
    config is known or the timeout ends (the still missing ones are abandoned
    with a log, the others are resumed).

    Args:
        configs (list[str]): Config names to wait for
        timeout (float): Seconds to wait at most. Defaults to
            RESUME_CONFIG_WAIT, read when the call runs (a default argument
            would bind the constant at import time and defeat monkeypatching)

    Returns:
        list[str]: Configs visible in the scan, input order
    """
    if timeout is None:
        timeout = RESUME_CONFIG_WAIT
    deadline = trio.current_time() + timeout
    while True:
        source = ConfigScanSource()
        await source.reinit()
        data = source.data
        ready = [config for config in configs if config in data]
        if len(ready) == len(configs):
            return ready
        if trio.current_time() >= deadline:
            missing = [config for config in configs if config not in data]
            logger.warning('[Restart] Resume abandoned, configs not found after '
                           f'{timeout:.0f}s: {", ".join(missing)}')
            return ready
        await trio.sleep(0.5)


async def _resume_one(manager, config: str):
    """
    Resolve one queued config and start it (failures are logged, not raised)

    Args:
        manager (WorkerManager): Worker manager
        config (str): Config name
    """
    try:
        from alasio.backend.topic.worker import get_mod
        mod = await get_mod(config)
    except Exception as e:
        # config / mod gone: drop the queue entry (it must not block a manual
        # start), log and continue with the rest
        logger.error(f'[Restart] Resume dropped, cannot load config "{config}": {e}')
        await trio.to_thread.run_sync(manager.worker_kill, config)
        return
    try:
        success, msg = await trio.to_thread.run_sync(manager.worker_resume, mod, config)
    except Exception as e:
        # the spawn failed: the entry is stuck in "starting" with no process,
        # clean it up so the config can be started again
        logger.error(f'[Restart] Resume failed: "{config}": {e}')
        await trio.to_thread.run_sync(manager.worker_force_kill, config)
        return
    if not success:
        # cancelled by the user, started by someone else, or superseded by a
        # new graceful restart (the entry is collected there)
        logger.info(f'[Restart] Resume skipped: "{config}": {msg}')
        return
    logger.info(f'[Restart] Worker resumed: {config}')


async def resume_after_restart(manager=None):
    """
    Consume the resume intent and start the recorded workers (new backend)

    Runs as a trio background task of the lifespan startup. Without a resume
    credential (no supervisor, cold start, killed session) nothing is read.
    The recorded workers are queued first (the frontend sees every one of them
    as "queued for resume"), then started one by one with WORKER_START_INTERVAL
    between two starts. A user stop on a queued config cancels its resume
    (worker_resume returns False, the queue skips it).

    Args:
        manager (WorkerManager): Manager to drive, defaults to
            BACKEND_WORKER_MANAGER (injectable for tests)
    """
    if manager is None:
        manager = BACKEND_WORKER_MANAGER
    GRACEFUL_RESTART.manager = manager
    queued = []
    with trio.CancelScope() as scope:
        GRACEFUL_RESTART.resume_scope = scope
        try:
            record = await trio.to_thread.run_sync(GRACEFUL_RESTART.read_resume)
            if record is None:
                return
            logger.info(f'[Restart] Resume intent accepted: {len(record.configs)} configs, '
                        f'{len(record.actions)} actions, owner={record.owner}')
            if record.actions:
                await trio.to_thread.run_sync(run_resume_actions, record.actions)
            configs = await _wait_configs_ready(record.configs)
            if not configs:
                logger.info('[Restart] Resume queue is empty, nothing to start')
                return
            queued = manager.mark_resume(configs)
            if not queued:
                logger.info('[Restart] Resume queue is empty, nothing to start')
                return
            push_restart_phase('resuming')
            logger.info(f'[Restart] Resume queue: {len(queued)} configs, '
                        f'starting with {WORKER_START_INTERVAL}s interval')
            for config in queued:
                if SHUTDOWN_EVENT.is_set():
                    logger.info(f'[Restart] Resume interrupted by the shutdown: {config}')
                    return
                await _resume_one(manager, config)
                # interval between two starts, measured from the worker_resume
                # return (do not wait for the worker to reach "running")
                await trio.sleep(WORKER_START_INTERVAL)
            push_restart_phase('done')
            # 'done' is transient: the phase disappears right after, "phase
            # present" means "a restart is in progress" for the frontend
            push_restart_phase('')
        except trio.Cancelled:
            # the cancel path (cancel_graceful_restart) owns the cleanup
            raise
        except Exception as e:
            logger.error(f'[Restart] Auto-resume failed: {e}')
            logger.exception(e)
            # release the still queued entries so they do not block a manual
            # start (worker_kill on a "resuming" entry = cancel the resume)
            for config in queued:
                try:
                    await trio.to_thread.run_sync(manager.worker_kill, config)
                except Exception:
                    pass
            push_restart_phase('')
        finally:
            if GRACEFUL_RESTART.resume_scope is scope:
                GRACEFUL_RESTART.resume_scope = None
