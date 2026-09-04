from typing import TYPE_CHECKING

import trio
from msgspec import DecodeError, ValidationError

from alasio.backend.mpipe.token_backend import token_table
from alasio.backend.reactive.base_topic import BaseTopic as BaseMixin
from alasio.backend.reactive.event import AccessDenied, ElectronOnlyError, ResponseEvent, RpcValueError
from alasio.backend.reactive.rx_trio import AsyncReactiveCallback, async_reactive_nocache
from alasio.ext.singleton import SingletonNamed
from alasio.logger import logger

if TYPE_CHECKING:
    # For IDE typehint, avoid recursive import
    from .ws_server import WebsocketTopicServer


class BaseTopic(AsyncReactiveCallback, BaseMixin, metaclass=SingletonNamed):
    # Topic-level electron restriction: when True, subscribing to this
    # topic and every rpc under it requires a valid electron token on the
    # connection (verified at operation time, never cached). Default
    # topics are public; mark the sensitive ones explicitly.
    REQUIRE_ELECTRON = False

    def __init__(self, conn_id, server: "WebsocketTopicServer"):
        """
        Create a data topic, that supports subscribe/unsubscribe
        and sends data changes once subscribed

        Args:
            conn_id (str):
        """
        self.conn_id = conn_id
        self.server = server
        # Current subscribed source instance; None when not subscribed.
        # Resubscription state (Trio thread only; plain flags, no lock):
        self._src = None
        self._busy = False   # a resubscribe round (incl. the catch-up loop) is running
        self._dirty = False  # a trigger arrived while busy -> catch up with the newest state
        self._closed = False  # op_unsub executed -> the running round must abort

    def __str__(self):
        return f'{self.topic_name()}({self.conn_id})>'

    def _check_electron(self):
        """
        Verify the connection's electron token in real time.

        Raises:
            ElectronOnlyError: When the connection has no valid token
        """
        if not token_table.verify(self.server.auth_token):
            raise ElectronOnlyError('Electron token required')

    @async_reactive_nocache
    async def _resubscribe(self):
        """
        Side-effect carrier of the source-model subscription: unsubscribe
        the old source, resolve the new subscription (get_source), register
        the new source and send the full event.

        Latest-wins merging (see doc/2026-09-03_topic-source-subscribe-v2.md):

        - at most ONE round runs per topic instance (entry gate `_busy`);
        - triggers arriving while a round runs (rapid navigation changes,
          dependency broadcasts) only set `_dirty` and return: they never
          start a second round, so the expensive full builds of the
          intermediate states are skipped;
        - when the running round finishes it drops its own result if
          `_dirty` is set (it is stale by then) and catches up with one
          more round reading the newest state; the loop ends when a round
          completes without new triggers (the last state is sent);
        - `op_unsub` sets `_closed`: the running round aborts at its next
          checkpoint, unregisters itself and never re-registers.

        The old `_gen` generation counter was removed: this instance only
        runs one round at a time, so no round can ever be "outdated" by a
        concurrent one. Serialization of full builds across connections
        sharing one source instance is the source's own job
        (ViewportEventSource single-flight).
        """
        if self._busy:
            # a round is running: merge this trigger into it
            self._dirty = True
            return
        self._busy = True
        try:
            while not self._closed:
                self._dirty = False
                old = self._src
                if old is not None:
                    old.unsubscribe(self)
                    self._src = None
                source = await self.get_source()
                if self._closed:
                    # unsubscribed while resolving: abort without registering
                    break
                if self._dirty:
                    # a newer trigger arrived while resolving: skip this
                    # round (no build) and catch up with the newest state
                    continue
                if source is None:
                    # no source right now: silent; the next dependency
                    # change re-runs this flow
                    break
                self._src = source
                snapshot = await source.subscribe(self)
                if self._closed:
                    # op_unsub ran while the build was in flight: undo the
                    # registration and abort
                    source.unsubscribe(self)
                    self._src = None
                    break
                if self._dirty:
                    # stale result: drop it (the next round unregisters)
                    # and catch up with the newest state
                    continue
                if snapshot is not None:
                    # Ordering contract: right after registration, without
                    # any await, send the full event so it precedes every
                    # later increment.
                    try:
                        self.server.send_nowait(snapshot)
                    except trio.WouldBlock:
                        # rare fallback; increments may sneak in during the
                        # await, which is an acceptable window (slow
                        # connection, dropped by heartbeat)
                        await self.server.send(snapshot)
                if self._dirty:
                    # a trigger arrived while sending: catch up once more
                    continue
                break
        finally:
            self._busy = False

    async def op_sub(self):
        """
        Subscribe to this topic, once subscribe the data will flow

        When receiving a "sub" event from client, the data flows
        --> Topic.get_source()
            the topic is bound to its source, the source snapshot is sent
            (subscribe() returns the encoded snapshot, sent immediately)

        Changes may come from:
        - backend background task that updates data
        - external database changes
        - another topic changes the dependency ot current topic
        - another client changes the data of current topic

        When a reactive dependency (ConnState) changes, the data flows:
        --> DataSource.data.mutate(self, data)
        --> @async_reactive_nocache
            changes will broadcast to callback function
            --> _resubscribe (unsubscribe the old source, resolve get_source
                again, bind the new source, send a new full)
        """
        await self._resubscribe

    async def get_source(self):
        """
        Resolve the source this topic should bind to.

        Returns:
            BaseSource | None:
                - the source instance to register; the initial full event
                  comes from source.subscribe() (cache sources snapshot
                  their data, viewport sources build their view);
                - None: no source right now, subscribe silently.

        Data preparation: topics that need fresh full data (ConfigScan /
        TaskQueue / one-shot sources / DevAssets) must `await
        source.reinit()` before returning. Sources without a full data
        source have an empty reinit, calling it costs nothing.

        May await ConnState etc. reactive dependencies; they form the
        observation chain that re-runs _resubscribe on changes, so a
        dependency change automatically re-binds the topic to the source
        resolved by the new conditions.
        """
        return None

    async def op_unsub(self):
        """
        Release current data topic
        """
        # Abort any running _resubscribe round: it checks `_closed` at its
        # next checkpoint, unregisters itself and never re-registers after
        # the connection is gone (no leak into resident sources).
        self._closed = True
        src = self._src
        if src is not None:
            src.unsubscribe(self)
            self._src = None
        cls = self.__class__
        cls.singleton_remove(self.conn_id)

    async def op_rpc(self, func, value, rpc_id):
        """
        Do RPC call on current topic

        Args:
            func (str): RPC method name
            value (Any): RPC method args
            rpc_id (str):
        """
        try:
            method = self.rpc_methods[func]
        except KeyError:
            msg = f'RPC method not found "{func}"'
            event = ResponseEvent(t=self.topic_name(), v=msg, i=rpc_id)
            await self.server.send(event)
            return

        # Electron check must happen BEFORE the call executes (never
        # inside the method body): a rejected request never ran, so a
        # renewal retry is a first execution, not a re-execution
        # (idempotency). Inside the try so the existing except branch
        # returns the error response carrying the rpc_id.
        try:
            if method.require_electron or self.REQUIRE_ELECTRON:
                self._check_electron()
            await method.call_async(self, value)
        except (ValidationError, DecodeError, UnicodeDecodeError, AccessDenied, RpcValueError) as e:
            # input errors
            msg = f'{e.__class__.__name__}: {e}'
            event = ResponseEvent(t=self.topic_name(), v=msg, i=rpc_id)
            await self.server.send(event)
            return
        except Exception as e:
            # unexpected internal errors
            logger.exception(e)
            msg = f'{e.__class__.__name__}: {e}'
            event = ResponseEvent(t=self.topic_name(), v=msg, i=rpc_id)
            await self.server.send(event)
            return

        # success
        # RPC success has no return value sent, omitting "v" means success, having "v" means error
        # The real RPC response will go through existing topic subscription
        event = ResponseEvent(t=self.topic_name(), i=rpc_id)
        await self.server.send(event)
        return
