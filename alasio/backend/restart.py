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
- the file is read at most once and always deleted right after the read;
- the publication and the withdrawal are two critical sections of one lock
  held by the tasks (GracefulRestart._publication_lock): the publication writes
  the file AND announces its credential, the withdrawal removes the file AND
  retracts the credential (announces an empty one; the supervisor slot is
  latest-wins). The lock is held by the task around the pool hop, not by the
  pool thread, so trio orders the two by itself: a transaction cancelled while
  it waits for the section is cancelled at the lock acquire (a checkpoint) and
  publishes nothing, and one cancelled while the section is held leaves its
  file and credential to the withdrawal, which waits for the section and
  retracts the credential last. A cancelled transaction therefore leaves no
  file behind, and a file whose removal failed is inert: "no credential
  announced => the file is never read" holds again, because the credential the
  write may have announced was taken back.

Credential handover (same session): the old backend announces the credential
with command:resume:<credential>; the supervisor stores it and injects it into
the next spawned backend as ALASIO_RESUME_TOKEN, then clears it once the new
backend announced startup completion (command:started). An empty credential is
the retraction: the stored slot is cleared and nothing is injected any more.
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

    One instance is one backend process: it binds the process-wide worker
    manager once, and besides the runtime state it owns the resume file IO
    (path, credential, write / read / cleanup) of its own transaction.

    Attributes:
        WORKER_MANAGER (WorkerManager): The backend worker manager singleton
            (BACKEND_WORKER_MANAGER), bound once and never reassigned; the
            orchestration takes it as the default `manager` argument and tests
            inject their own there
        running (bool): True while this backend owns a restart transaction.
            Set synchronously by the rpc handler (re-entry guard), cleared by
            cancel_graceful_restart(); kept on the success path, the gate stays
            until the process exits
        scope (trio.CancelScope): Orchestration task scope, cancelled and
            dropped by cancel_graceful_restart()
        resume_scope (trio.CancelScope): Resume task scope of the new backend
        resume_file (PathStr): Resume file published by this transaction (the
            withdrawal removes it and retracts its credential), None when
            nothing was published yet
        resume_owner (str): Owner of the published resume file
    """

    # the worker manager of this backend process, bound once: the orchestration
    # takes it as its default `manager` argument (tests pass their own)
    WORKER_MANAGER = BACKEND_WORKER_MANAGER

    def __init__(self):
        self.running = False
        self.scope = None
        self.resume_scope = None
        self.resume_file: "Optional[PathStr]" = None
        self.resume_owner = ''
        # critical sections of the resume publication, held by the tasks (not by
        # the pool threads): write_resume() writes the file and announces the
        # credential inside it, withdraw_resume() removes the file and retracts
        # the credential inside it. A task that only waits for the section is
        # cancelled at the acquire (a checkpoint), so a cancelled transaction
        # publishes nothing; one that holds the section is left to run to
        # completion and is withdrawn afterwards
        self._publication_lock = trio.Lock()
        # resume_folder is a cached_property: drop the cache on a re-init
        # (reset(), a caller repointing PROJECT_ROOT) so the current
        # PROJECT_ROOT is read again
        cached_property.pop(self, 'resume_folder')

    def reset(self):
        """
        Reset to the initial state (test helper)
        """
        self.__init__()

    def restart_in_progress(self) -> bool:
        """
        Whether a restart transaction (or its resume queue) is in flight

        `running` is the rpc re-entry flag: set on the click, cleared by the
        cancel and kept on the success path until the process exits. The scopes
        cover the tasks themselves: the orchestration of the old backend and the
        resume queue of the new one.

        Returns:
            bool: True when cancel_graceful_restart() actually interrupts
                something (an idle backend has nothing to cancel)
        """
        return bool(self.running or self.scope is not None or self.resume_scope is not None)

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

    async def withdraw_resume(self):
        """
        Withdraw this transaction's publication: remove the file, retract its
        credential (cancel)

        The mirror of write_resume(), in the same critical section: the
        publication writes the file and announces the credential, the withdrawal
        removes the file and announces '' (the supervisor slot is latest-wins,
        so the empty announcement takes the credential back). The lock is held by
        the task, so a publication in flight is left to run to completion first,
        and a publication still waiting for the section never happens at all
        (its task is cancelled at the acquire): whatever the timing, the
        retraction is the last word the supervisor hears about this transaction,
        and a file whose removal failed is inert from then on. The blocking work
        (the removal and the pipe send) runs in the trio thread pool, inside the
        section. cancel_graceful_restart() calls it right after it cancelled the
        orchestration, inside a shielded scope: a cancel must complete.

        An idle cancel is a no-op, and the file of another owner (the in-app
        update transaction) is left completely alone: that transaction keeps its
        file and its credential (update architecture §4.6).

        Returns:
            PathStr | None: The removed file, None when there was nothing to
                remove (nothing published, or published by another owner)
        """
        async with self._publication_lock:
            file = self.resume_file
            owner = self.resume_owner
            if file is None or owner != OWNER_RESTART:
                # nothing of this transaction to withdraw: no file of ours to
                # remove and no credential of ours to retract
                return None
            self.resume_file = None
            self.resume_owner = ''
            removed = await trio.to_thread.run_sync(self._remove_and_retract, file)
        if not removed:
            return None
        logger.info(f'[Restart] Resume file removed by the cancel: {file}')
        return file

    def _remove_and_retract(self, file):
        """
        Remove the resume file and retract its credential (blocking, pool only)

        Called inside the withdrawal section, after the file was taken out of the
        slot.

        Args:
            file (PathStr): The file published by this transaction

        Returns:
            bool: True when the file was removed
        """
        try:
            removed = atomic_remove(file)
        except OSError as e:
            logger.warning(f'[Restart] Failed to remove the resume file {file}: {e}')
            removed = False
        # the retraction is announced after the removal (failed or not) and
        # always after the announcement the write made in the same section: the
        # supervisor only keeps this empty credential from now on
        self.announce_resume_token('')
        return removed

    async def write_resume(self, resume_list, owner=OWNER_RESTART, actions=None) -> str:
        """
        Publish the resume intent: write the file and announce its credential

        The publication section of this transaction: async, with the blocking
        work (disk write + supervisor pipe) in the trio thread pool, so the event
        loop stays responsive.

        Called once per restart transaction, after restart_wait() returned (never
        during the wait): the content is the final resume list, there is no
        intermediate write and no rewrite. The file hits the disk and the
        credential is announced inside the section, so the file exists before the
        supervisor can hand the credential to the next backend ("no credential
        announced => the file is never read"), a call site cannot forget the
        announce, and no cancel can interleave between the file and its
        credential. The lock is held by this task: a transaction cancelled while
        this call waits for the section is cancelled at the acquire (a
        checkpoint) and publishes nothing, and one cancelled while the section is
        held leaves the file and the credential to the withdrawal, which waits
        for the section and retracts the credential afterwards (F3). Only the
        file of this transaction is written: other resume files in the folder
        (another backend, a session that died before its transaction finished)
        are left untouched -- they can never be consumed without their own
        credential, and the stale ones are removed by resume_cleanup().

        Args:
            resume_list (list[str]): Configs to auto-resume after the restart
            owner (str): Transaction owner, OWNER_RESTART ('restart') or 'update'
            actions (list[str]): Optional action tags the new backend runs before
                the resume

        Returns:
            str: Credential string f'{token}-{checksum}', already announced to
                the supervisor
        """
        async with self._publication_lock:
            file, credential = await trio.to_thread.run_sync(
                self._write_and_announce, resume_list, owner, actions)
            # the slot belongs to the trio thread; the section stays held until
            # the withdrawal can see what this call published
            self.resume_file = file
            self.resume_owner = owner
        return credential

    def _write_and_announce(self, resume_list, owner, actions):
        """
        Write the resume file and announce its credential (blocking, pool only)

        Called inside the publication section: the file and its credential are
        one step and are never separated by a cancel.

        Args:
            resume_list (list[str]): Configs to auto-resume after the restart
            owner (str): Transaction owner
            actions (list[str]): Optional action tags

        Returns:
            tuple[PathStr, str]: The written file and its credential
        """
        token = secrets.token_hex(16)
        record = ResumeRecord(
            ts=time.time(),
            owner=owner,
            configs=list(resume_list),
            actions=list(actions) if actions else [],
        )
        payload = msgspec.json.encode(record)
        credential = f'{token}-{self.resume_checksum(token, payload)}'
        file = self.resume_file_of(token)
        atomic_write(file, payload)
        # the write is complete before the credential leaves this method: the
        # supervisor only ever learns about a file that is already on disk
        self.announce_resume_token(credential)
        logger.info(f'[Restart] Resume file written: {file} '
                    f'(owner={owner}, {len(record.configs)} configs, {len(record.actions)} actions)')
        return file, credential

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
        Announce (or retract) the resume credential over the supervisor pipe

        Called by write_resume() right after the file hit the disk and by
        withdraw_resume() for its retraction (blocking: the pipe send runs in
        the caller's thread pool hop).

        Sends b'command:resume:<credential>'; the supervisor stores the latest
        announcement and injects it into the next spawned backend
        (ALASIO_RESUME_TOKEN). An empty credential is the retraction: the stored
        slot is cleared and nothing is injected any more, so a cancelled
        transaction cannot be consumed whatever the state of its file. Without a
        supervisor the message is silently dropped (send default), so a file
        written without a supervisor is never consumed: the credential cannot be
        handed over.

        Args:
            credential (str): Credential string returned by write_resume(), or
                '' to retract the announced one
        """
        # the file exists before the credential leaves this method, and the cancel
        # announces the retraction after the write (the publication section orders
        # the two): the invariant "no credential announced => the file is never
        # read" holds in both directions
        mpipe_backend.send(b'command:resume:' + credential.encode())
        if credential:
            logger.info('[Restart] Resume credential announced to the supervisor')
        else:
            logger.info('[Restart] Resume credential retracted from the supervisor')


GRACEFUL_RESTART = GracefulRestart()


# =============================================================================
# Restart topic phase
# =============================================================================

def _push_restart_phase(phase: str):
    """
    Push the restart phase to the Restart topic (blocking, thread pool only)

    Args:
        phase (str): '' clears the topic, otherwise 'stopping' /
            'shutting-down' / 'resuming' / 'done'
    """
    RestartSource().on_event(phase)


async def push_restart_phase(phase: str):
    """
    Push the restart phase to the Restart topic (async wrapper)

    The source event entry is a sync function (it locks the source and queues
    the delivery), so it runs in the trio thread pool -- the event loop never
    executes it.

    Args:
        phase (str): '' clears the topic, otherwise 'stopping' /
            'shutting-down' / 'resuming' / 'done'
    """
    try:
        await trio.to_thread.run_sync(_push_restart_phase, phase)
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

async def run_graceful_restart(manager=GRACEFUL_RESTART.WORKER_MANAGER, hooks=None):
    """
    Graceful restart orchestration (old backend)

    Runs as a trio background task (the rpc returns immediately): the graceful
    stop can last up to GRACEFUL_STOP_TIMEOUT and the frontend keeps watching
    the worker states through the Worker topic while it waits.

    The resume file is written once the wait is over -- never during it -- and
    the credential is announced inside that write. Every call into the blocking
    layer (the manager, the resume file IO, the restart topic push, the backend
    restart itself) goes through the trio thread pool, so the event loop stays
    responsive while the frontend watches the worker states through the Worker
    topic. On the success path the manager gate is kept (until the process
    exits): a worker started in the restart window would be killed at exit and
    never resumed.

    Args:
        manager (WorkerManager): Manager to drive, defaults to
            GRACEFUL_RESTART.WORKER_MANAGER (the process singleton, injectable
            for tests)
        hooks (RestartHooks): Optional hooks of the in-app update flow
    """
    with trio.CancelScope() as scope:
        GRACEFUL_RESTART.scope = scope
        try:
            try:
                # blocking: snapshots the running workers and sends the graceful
                # stop requests over the worker pipes
                waiting = await trio.to_thread.run_sync(manager.restart_begin)
            except Exception as e:
                # already restarting (the rpc re-entry guard makes this
                # unreachable) or nothing to begin: release the rpc flag
                logger.error(f'[Restart] Graceful restart cannot begin: {e}')
                GRACEFUL_RESTART.running = False
                return

            await push_restart_phase('stopping')
            # the waiting set of restart_begin() is the whole set this restart
            # waits for (the final resume list is the second element of the
            # restart_wait() result below)
            logger.info(
                f'[Restart] Graceful restart requested, '
                f'waiting up to {GRACEFUL_STOP_TIMEOUT:.0f}s for the workers to stop, '
                f'waiting for: {waiting}')

            # 1) block for every worker to stop; the wait lives in the manager
            #    (thread-safe, no trio), the timeout escalation happens inside
            #    restart_wait and the abort event makes it return early. Its
            #    success flag is the whole verdict of the wait: a cancelled one
            #    (force restart / backend stop) must never write a resume file
            #    and never restart
            success, resume_list = await trio.to_thread.run_sync(
                manager.restart_wait, GRACEFUL_STOP_TIMEOUT)
            if not success:
                # cancelled while waiting (force restart / backend stop); the
                # cancel path already owns the state cleanup
                logger.info('[Restart] Graceful restart was cancelled')
                return

            # 2) the wait is over: publish the resume intent once (the file and
            #    its credential are one step; the credential is announced inside
            #    the write). A cancel landing while the section is held waits for
            #    it and withdraws it, one landing before it cancels this task at
            #    the lock acquire instead. The list is frozen from here on: a
            #    per-config cancel of a config of this list is refused from now
            #    on, because this file resumes it whatever the request does; a
            #    "restarting" entry outside the list carries no resume intent
            #    and stays cancellable
            actions = getattr(hooks, 'actions', None)
            if resume_list or actions:
                await GRACEFUL_RESTART.write_resume(resume_list, OWNER_RESTART, actions)
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
            await push_restart_phase('shutting-down')
            logger.info(f'[Restart] All workers stopped, restarting backend, resume list: {resume_list}')
            await lifespan_restart()
        except trio.Cancelled:
            # the cancel path (cancel_graceful_restart) owns the cleanup
            raise
        except Exception as e:
            await cancel_graceful_restart(f'graceful restart failed: {e}', manager)
            logger.error(f'[Restart] Graceful restart failed: {e}')
            logger.exception(e)
            raise
        finally:
            if GRACEFUL_RESTART.scope is scope:
                GRACEFUL_RESTART.scope = None


# =============================================================================
# Cancel
# =============================================================================

async def cancel_graceful_restart(reason: str = '', manager=GRACEFUL_RESTART.WORKER_MANAGER):
    """
    Cancel the graceful restart in progress (idempotent)

    Async: the rpc methods await it, the mpipe shutdown thread (lifespan.py)
    drives it through trio.from_thread.run(). Everything blocking inside (the
    manager reset, the resume file removal, the topic push) runs in the trio
    thread pool.

    Cancels the orchestration task (old backend) and the resume task (new
    backend), releases the manager gate, removes the resume file this
    transaction wrote (owner='restart' only: an update-owned file belongs to
    the update transaction) and clears the Restart topic.

    A cancellation is only logged when there was something to interrupt: a
    force restart (or a backend stop) of an idle backend cancels nothing and
    must not claim it cancelled a graceful restart.

    Args:
        reason (str): Log message
        manager (WorkerManager): Manager whose gate / marks are released,
            defaults to GRACEFUL_RESTART.WORKER_MANAGER
    """
    # the cleanup must complete even when the caller runs inside a cancelled
    # scope (the orchestration task being cancelled, a backend shutdown): every
    # await below is shielded
    with trio.CancelScope(shield=True):
        # read before the cleanup below resets it: only a transaction actually
        # in flight reports the cancellation (the cleanup itself is idempotent
        # and runs unconditionally: it is the shutdown guarantee)
        in_progress = GRACEFUL_RESTART.restart_in_progress()
        # 1) cancel the orchestration / resume task: they must not continue with
        #    the restart or the resume queue. This runs on the trio thread (the
        #    rpc / trio.from_thread.run / the orchestration itself), so the
        #    cancel scopes are touched directly; a scope that already exited
        #    ignores the cancel. The waiting thread stops through the manager
        #    abort event below
        if GRACEFUL_RESTART.scope is not None:
            GRACEFUL_RESTART.scope.cancel()
        if GRACEFUL_RESTART.resume_scope is not None:
            GRACEFUL_RESTART.resume_scope.cancel()
        GRACEFUL_RESTART.running = False
        # the transaction is gone: the scope slot is dropped here (rather than
        # left to the orchestration's finally) so restart_in_progress() is false
        # as soon as the cancel ran; the finally then sees the slot moved on and
        # leaves it alone
        GRACEFUL_RESTART.scope = None
        # 2) release the manager gate, clear the marks, drop the parked entries
        #    (blocking: manager lock)
        try:
            await trio.to_thread.run_sync(manager.restart_cancel)
        except Exception as e:
            logger.warning(f'[Restart] Failed to cancel the manager state: {e}')
        # 3) withdraw the publication of this transaction: the lock is held by
        #    the tasks, so a publish still waiting for the section never happens
        #    (its task is cancelled at the acquire) and one in flight is left to
        #    run to completion, then removed and retracted. The retraction is the
        #    last word the supervisor hears, which makes a cancelled restart
        #    unresumable whatever the file does (F3)
        try:
            await GRACEFUL_RESTART.withdraw_resume()
        except Exception as e:
            logger.warning(f'[Restart] Failed to withdraw the resume publication: {e}')
        # 4) no restart in progress any more
        await push_restart_phase('')
        if in_progress:
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


def _resume_one(manager, config: str):
    """
    Resolve one queued config and start it (blocking, thread pool only)

    Failures are logged, not raised: one broken config must not stop the queue.

    Args:
        manager (WorkerManager): Worker manager
        config (str): Config name
    """
    try:
        from alasio.backend.topic.worker import get_mod
        mod = get_mod(config)
    except Exception as e:
        # config / mod gone: drop the queue entry (it must not block a manual
        # start), log and continue with the rest
        logger.error(f'[Restart] Resume dropped, cannot load config "{config}": {e}')
        manager.worker_kill(config)
        return
    try:
        success, msg = manager.worker_resume(mod, config)
    except Exception as e:
        # the spawn failed: the entry is stuck in "starting" with no process,
        # clean it up so the config can be started again
        logger.error(f'[Restart] Resume failed: "{config}": {e}')
        manager.worker_force_kill(config)
        return
    if not success:
        # cancelled by the user, started by someone else, or superseded by a
        # new graceful restart (the entry is collected there)
        logger.info(f'[Restart] Resume skipped: "{config}": {msg}')
        return
    logger.info(f'[Restart] Worker resumed: {config}')


async def resume_after_restart(manager=GRACEFUL_RESTART.WORKER_MANAGER):
    """
    Consume the resume intent and start the recorded workers (new backend)

    Runs as a trio background task of the lifespan startup. Without a resume
    credential (no supervisor, cold start, killed session) nothing is read.
    The recorded workers are queued first (the frontend sees every one of them
    as "queued for resume"), then started one by one with WORKER_START_INTERVAL
    between two starts. A user stop on a queued config cancels its resume
    (worker_resume returns False, the queue skips it).

    Every call into the blocking layer (the resume file read, the actions, the
    manager queue, the worker starts, the topic pushes) goes through the trio
    thread pool. The stale resume file cleanup is part of this task and runs
    after the read (never as an independent task: it must not race the file
    this backend consumes).

    Args:
        manager (WorkerManager): Manager to drive, defaults to
            GRACEFUL_RESTART.WORKER_MANAGER (the process singleton, injectable
            for tests)
    """
    queued = []
    with trio.CancelScope() as scope:
        GRACEFUL_RESTART.resume_scope = scope
        try:
            # 1) consume the file of THIS session first: read_resume deletes it,
            #    so the stale file cleanup below can never race it
            record = None
            try:
                record = await trio.to_thread.run_sync(GRACEFUL_RESTART.read_resume)
            except Exception as e:
                # an unexpected failure of the read must not skip the housekeeping
                logger.error(f'[Restart] Resume file read failed: {e}')
                logger.exception(e)
            # 2) then the leftovers of sessions killed before their transaction
            #    finished. Same task, strict order: the two must never run as
            #    independent tasks (the cleanup would race the file just read)
            await trio.to_thread.run_sync(GRACEFUL_RESTART.resume_cleanup)
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
            # blocking: the queue takes the manager lock and parks the entries
            queued = await trio.to_thread.run_sync(manager.mark_resume, configs)
            if not queued:
                logger.info('[Restart] Resume queue is empty, nothing to start')
                return
            await push_restart_phase('resuming')
            logger.info(f'[Restart] Resume queue: {len(queued)} configs, '
                        f'starting with {WORKER_START_INTERVAL}s interval')
            for config in queued:
                if SHUTDOWN_EVENT.is_set():
                    logger.info(f'[Restart] Resume interrupted by the shutdown: {config}')
                    return
                # blocking (mod resolution + process spawn) -> thread pool
                await trio.to_thread.run_sync(_resume_one, manager, config)
                # interval between two starts, measured from the worker_resume
                # return (do not wait for the worker to reach "running")
                await trio.sleep(WORKER_START_INTERVAL)
            await push_restart_phase('done')
            # 'done' is transient: the phase disappears right after, "phase
            # present" means "a restart is in progress" for the frontend
            await push_restart_phase('')
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
            await push_restart_phase('')
        finally:
            if GRACEFUL_RESTART.resume_scope is scope:
                GRACEFUL_RESTART.resume_scope = None
