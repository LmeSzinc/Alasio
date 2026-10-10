"""
Runtime context of the graceful restart: the process singleton
(GRACEFUL_RESTART), its runtime state and the resume file protocol.

The orchestration that drives it -- the restart window, the restart entries,
the cancel path and the startup resume queue -- lives in
alasio.backend.app.restart: the dependency goes one way (restart -> context),
the context never reaches back into the orchestration.

File protocol:
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

from alasio.backend.mpipe.mpipe_backend import mpipe_backend
from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER
from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, atomic_remove, atomic_write
from alasio.logger import logger

# Age of a resume file that counts as a stale leftover: nothing stale can be
# consumed (a read requires the one-shot credential), this only keeps the disk
# clean after a session was killed before its transaction finished
RESUME_CLEANUP_AGE = 3 * 24 * 3600.0
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
            Set synchronously by the entries (re-entry guard), cleared by
            cancel_graceful_restart(); kept on the success path, the gate stays
            until the process exits
        scope (trio.CancelScope): Orchestration task scope, cancelled and
            dropped by cancel_graceful_restart()
        window (RestartWindow): The window of the restart in flight (the one
            of the default driver or the one the update flow drives itself),
            None when no restart was opened. An external cancel marks it
            withdrawn: its shutdown is then refused (the process keeps
            running)
        holder (str): Description of the internal owner of the backend (the
            update transaction, 'update of "m"'), '' when none. The public
            entry request_graceful_restart() refuses while a holder is
            registered, open_restart_window() is the entry of the holder
            itself
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
        self.window = None
        self.holder = ''
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
        # The takeover lock: the critical section of "who owns the backend"
        # between the auto-resume queue of the new backend and a restart that
        # takes over. Four holders, all on the trio thread and only for short
        # local work (file IO / memory, no network, no long wait):
        # - the auto-resume task, around its preparation (read the resume file,
        #   clean stale leftovers, mark the configs in the manager) and around
        #   every phase it pushes (with the restart gate checked inside);
        # - the orchestration, around restart_begin() and its first phase
        #   ("stopping") -- this is what makes a restart wait for a preparation
        #   in flight, so the marks of a consumed resume file always exist
        #   before the restart collects them;
        # - cancel_graceful_restart(), around the manager reset and the topic
        #   clear (the resume scope is cancelled before, so the task aborts at
        #   its next await and releases the lock there).
        # Lock order everywhere is _takeover_lock -> manager lock (the manager
        # never takes the former), and the sections are bounded by the local IO
        # above: the cancel path cannot be delayed beyond that.
        self._takeover_lock = trio.Lock()
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

    def set_holder(self, holder):
        """
        Register the internal owner of the backend (the update transaction)

        Called in the same synchronous section that opens the update
        transaction (no await in between): the registry and the transaction
        can never drift apart, and an external restart requested right after
        sees the new holder.

        Args:
            holder (str): Description of the owner, e.g. 'update of "m"'
        """
        self.holder = holder

    def clear_holder(self, holder):
        """
        Clear the registration of the internal owner

        Only the registration of that holder is cleared: a stale close never
        drops a newer owner (the same rule as the update window of the worker
        manager).

        Args:
            holder (str): The description registered before
        """
        if self.holder == holder:
            self.holder = ''

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
        file and its credential.

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
        A file that cannot be read at all is removed best effort too, except
        when the read was denied by permissions (the denial is taken as covering
        the removal): the one-shot credential has no holder left, so nobody can
        ever consume the file again.

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
        payload = self._read_resume_file(file)
        if payload is None:
            return None
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

    def _read_resume_file(self, file):
        """
        Read the resume file, delete it, and return its payload

        The read and the deletion are one step (read-once): a file that was read
        never survives, whatever the verification result of its payload. A file
        whose read failed is removed by the same call below -- this read was the
        only holder of the one-shot credential, so nobody can ever consume it
        again. The deletion is best effort: a failure only logs and leaves the
        file to the 3 day stale cleanup, the payload (when there is one) is
        returned all the same. Two failures return early: a missing file has
        nothing to delete, and a permission denial is taken as covering the
        removal as well (no attempt, and no retry loop on Windows).

        Args:
            file (PathStr): The file named by the credential

        Returns:
            bytes | None: The payload, or None (missing or unreadable)
        """
        try:
            payload = atomic_read_bytes(file)
        except FileNotFoundError:
            logger.info(f'[Restart] Resume file not found: {file}')
            return None
        except PermissionError as e:
            logger.warning(f'[Restart] Resume file not readable: {file}: {e}')
            # the denial is taken as covering the removal too: no attempt (and
            # no retry loop on Windows), the stale cleanup takes it
            return None
        except OSError as e:
            logger.warning(f'[Restart] Resume file not readable: {file}: {e}')
            # fall through to the removal below: this read was the only holder
            # of the one-shot credential, so the file can never be consumed any
            # more and must not linger for the 3 day stale cleanup
            payload = None
        # read once: the file never survives a read, valid or not (an unreadable
        # file falls through to the same removal; a missing file and a permission
        # denial returned early above)
        try:
            atomic_remove(file)
        except OSError as e:
            # best effort: a failed removal only leaves the file to the 3 day
            # stale cleanup, the payload is returned all the same
            logger.warning(f'[Restart] Failed to remove the resume file {file}: {e}')
        return payload

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

    # =========================================================================
    # Auto-resume task of the new backend
    # =========================================================================


GRACEFUL_RESTART = GracefulRestart()
