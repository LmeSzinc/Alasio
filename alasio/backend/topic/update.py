"""
Update topic: the update state of every mod (data = dict[mod_name,
UpdateInfo]), pushed by the update manager (alasio.backend.app.update_manager).

The state pipeline of one mod:

    checking -> uptodate / available -> downloading -> updating

The data is runtime-produced and resident: the update manager pushes one
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
# - 'downloading': the update transaction downloads and validates the
#   update into memory (nothing on disk changes), cancellable
# - 'updating': the transaction applies the update (stop / replace /
#   restart / resume); local and irreversible, not cancellable
# - 'error': the last check or the update failed, see UpdateInfo.error
UPDATE_STATE = [
    'unmanaged', 'idle', 'checking', 'uptodate',
    'available', 'downloading', 'updating', 'error',
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
    # unix timestamp of the last check that finished, 0 = never
    checked_at: float = 0.
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
    async def update_check(self, name: str = ''):
        """
        Check the updates now

        The check of one mod runs in the loop of that mod: this resets its
        sequence (the manual check counts as the first one, the next
        automatic one waits the standard random delay) and runs
        immediately, an in-flight check of the mod is interrupted and
        restarted. Returns once the checks are requested, the progress
        flows through the Update topic.

        Args:
            name (str): Mod name, '' checks every managed mod
        """
        from alasio.backend.app.update_manager import UPDATE_MANAGER, UpdateError

        try:
            await UPDATE_MANAGER.check(name)
        except UpdateError as e:
            raise RpcValueError(str(e)) from None

    @rpc
    async def update_apply(self, name: str):
        """
        Apply the available update of a mod

        The transaction is accepted and runs in the background: it first
        downloads the update into memory ('downloading', cancellable
        through update_cancel), then stops every worker, applies the update
        and restarts the backend ('updating'). Returns once the transaction
        is accepted, the progress flows through the Update topic (the
        workers through the Worker topic and the restart through the
        Restart topic).

        Args:
            name (str): Mod name
        """
        from alasio.backend.app.update_manager import UPDATE_MANAGER, UpdateError

        try:
            await UPDATE_MANAGER.apply(name)
        except UpdateError as e:
            raise RpcValueError(str(e)) from None

    @rpc
    async def update_cancel(self):
        """
        Cancel the cancellable phase of the update flow

        A check in flight: its request is interrupted and the window
        closes, the recorded state of the last check stays. The download of
        an update transaction: the prepared data is dropped, nothing was
        changed on disk and the state returns to 'available'. The apply of
        a transaction is not cancellable and refuses this call.
        """
        from alasio.backend.app.update_manager import UPDATE_MANAGER, UpdateError

        try:
            await UPDATE_MANAGER.cancel()
        except UpdateError as e:
            raise RpcValueError(str(e)) from None
