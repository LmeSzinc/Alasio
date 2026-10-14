"""
Update topic: the update state of every mod (data = dict[mod_name,
UpdateInfo]), pushed by the update check module
(alasio.backend.app.restart.update_check) and by the apply steps of the
maintenance flows (alasio.backend.app.restart.restart_step).

The state pipeline of one mod:

    checking -> uptodate / available -> queued -> stopping -> downloading -> updating

The data is runtime-produced and resident: the check module pushes one
event per change and the source keeps the data for a later front-end
visit (no disk fallback, losing an event would lose the state).
"""
from typing import Dict

from msgspec import Struct

from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import ResponseEvent, RpcValueError
from alasio.backend.reactive.source import GlobalSource, ResidentCache
from alasio.backend.ws.ws_topic import BaseTopic

# States of a mod update:
# - 'unmanaged': the mod declares no update source (no mirrors); it is
#   never checked
# - 'idle': never checked, or the checks wait for a manual request
# - 'checking': a check is in flight (get_latest_info), cancellable
# - 'uptodate': the local version is the latest one
# - 'available': an update is available and waits for the user
# - 'queued': the update flow of the mod was accepted (the workers are
#   being stopped, the check sequences paused)
# - 'stopping': the workers stopped for the update of the mod, the pack is
#   about to be prepared
# - 'downloading': the update pipeline downloads and validates the update
#   into memory (nothing on disk changes), cancellable
# - 'updating': the flow applies the update (the real files are being
#   replaced); local and irreversible, not cancellable
# - 'restart-required': the files were applied but the handover of the flow
#   failed: the next process start converges them
# - 'error': the last check or the update failed, see UpdateInfo.error
UPDATE_STATE = [
    'unmanaged', 'idle', 'checking', 'uptodate',
    'available', 'queued', 'stopping', 'downloading', 'updating',
    'restart-required', 'error',
]


class UpdateInfo(Struct):
    """
    Update state of one mod, the value of the Update topic.

    Never mutated after it is bound to the topic data: every change pushes
    a new instance (the incremental payload of an event is encoded outside
    the lock of the source).
    """
    # one of UPDATE_STATE
    state: str
    # version of the local index pack of the mod, '' when it is missing
    current_version: str = ''
    # latest version seen by the last succeeded check, '' when unknown
    latest_version: str = ''
    # unix timestamp (seconds) of the last check that finished, 0 = never.
    # An int on purpose: the sub-second part is meaningless to the frontend
    # (the seconds are what the cards show) and the topic is pushed on every
    # change
    checked_at: int = 0
    # failure message of the last error, '' when there is none
    error: str = ''


class UpdateSource(GlobalSource, ResidentCache):
    """
    Update states of every mod: data = dict[mod_name, UpdateInfo], resident
    (runtime-produced state kept for later front-end visits).

    Event protocol: on_event((mod_name, info | None)); None deletes the
    entry, any other value sets the entry of that mod.
    """
    TOPIC_NAME = 'Update'
    data: "Dict[str, UpdateInfo]"

    def _apply(self, event):
        """
        [锁内] (mod_name, info) -> data; info=None deletes the entry

        Returns:
            bool: If data changed
        """
        name, info = event
        if info is None:
            if name not in self.data:
                return False
            del self.data[name]
            return True
        if self.data.get(name, None) is info:
            return False
        self.data[name] = info
        return True

    def _convert(self, event):
        """
        [锁内] The incremental response of the event; the value is the
        event payload itself, never mutated afterwards

        Returns:
            ResponseEvent:
        """
        name, info = event
        if info is None:
            return ResponseEvent(t=self.TOPIC_NAME, o='del', k=(name,))
        return ResponseEvent(t=self.TOPIC_NAME, o='set', k=(name,), v=info)


class Update(BaseTopic):
    TOPIC_NAME = 'Update'

    async def get_source(self):
        """
        Update states flow entirely through events (UpdateManager ->
        UpdateSource): no reinit needed.
        """
        return UpdateSource()

    @rpc
    async def update_check(self, name: str):
        """
        Check the updates of one mod now

        The check runs in this call (it is one request to the mirrors) and the
        returned message tells what happened: '' when the check ran, the reason
        it was discarded otherwise (a check that just finished, a check of the
        mod already in flight). A check in flight of the mod is interrupted and
        the sequence starts over after the check.

        Args:
            name (str): Mod name
        """
        from alasio.backend.app.restart.update_check import UPDATE_CHECK

        try:
            return await UPDATE_CHECK.update_check(name)
        except ValueError as e:
            raise RpcValueError(str(e)) from None

    @rpc
    async def cancel_check(self, name: str):
        """
        Cancel the check of one mod in flight

        Args:
            name (str): Mod name: the check request of that mod is interrupted
                and the recorded state of the last check stays
        """
        from alasio.backend.app.restart.update_check import UPDATE_CHECK

        if not UPDATE_CHECK.cancel_check(name):
            raise RpcValueError(f'No check is in flight: "{name}"')

    @rpc
    async def update_apply(self):
        """
        Apply the available updates of every mod

        The update flow is accepted and runs in the background: it stops the
        workers, applies the deploy job of every mod with an available update
        (cancel_apply interrupts it until its first real write, a failed mod is
        isolated) and restarts the backend. Returns once the flow is accepted,
        the progress flows through the Update topic (the workers through the
        Worker topic and the restart through the Restart topic).
        """
        from alasio.backend.app.restart.restart_manager import RESTART_MANAGER, FlowRefused
        from alasio.backend.app.restart.update_check import UPDATE_CHECK

        if not UPDATE_CHECK.available():
            raise RpcValueError('No update available')
        try:
            RESTART_MANAGER.update_apply()
        except FlowRefused as e:
            raise RpcValueError(str(e)) from None

    @rpc
    async def cancel_apply(self):
        """
        Cancel the update flow in flight

        The flow is cancellable until its first real write (the worker stop and
        the download); past that point of no return, and after an upgrade into
        the daily maintenance, the cancel is refused. The wrap-up of the flow
        resumes the workers it stopped and restarts the check sequences it
        paused.
        """
        from alasio.backend.app.restart.restart_manager import RESTART_MANAGER, FlowRefused

        try:
            await RESTART_MANAGER.cancel_apply()
        except FlowRefused as e:
            raise RpcValueError(str(e)) from None
