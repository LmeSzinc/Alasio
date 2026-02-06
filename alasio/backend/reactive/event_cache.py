import threading
import time
from typing import Optional

import trio
from msgspec.json import encode
from trio._core import TrioToken

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.worker.event import ConfigEvent
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.ext.deep import deep_iter_patch, deep_set
from alasio.ext.singleton import Singleton, SingletonNamed
from alasio.logger import logger


def deep_iter_event(topic, before, after):
    for op, keys, value in deep_iter_patch(before, after):
        yield ResponseEvent(t=topic, o=op, k=keys, v=value)


class EventCache:
    TTL = 5
    TOPIC = ''

    def __init__(self):
        self.subscribers: "set[BaseTopic]" = set()
        self._fetch_lock = trio.Lock()
        self._data_lock = threading.Lock()
        self.data = {}
        self._running = False
        self._lastrun = 0.

        if not self.TOPIC:
            logger.warning(f'{self.__class__.__name__}.TOPIC is not set')

    def on_init(self):
        return {}

    async def on_init_async(self):
        return await trio.to_thread.run_sync(self.on_init)

    def _broadcast_update(self, data: bytes):
        """
        发送更新事件给所有订阅者

        原理：Trio 是协作式调度的。只要在这个 for 循环里没有 `await`，
        就没有任何代码能运行 `bridge` 里的 subscribers.remove/add。
        集合结构就是安全的。
        """
        for ch in self.subscribers:
            ch.server.send_lossy(data)

    def on_event(self, event: ConfigEvent, trio_token: TrioToken):
        """
        [子线程 / Pipe接收端]
        """
        with self._data_lock:
            if event.k:
                deep_set(self.data, keys=event.k, value=event.v)
            else:
                self.data = event.v
            # 只要收到更新，就刷新时间
            self._lastrun = time.monotonic()
            self._running = True

            # encode event first, so data won't be changed during send
            event = ResponseEvent(t=event.t, o='set', k=event.k, v=event.v)
            event = encode(event)

            # schedule send event within lock, and run outside of lock
            try:
                trio_token.run_sync_soon(self._broadcast_update, event)
            except trio.RunFinishedError:
                pass

    async def fetch_init(self, force=False):
        if not force:
            with self._data_lock:
                # 优先级 1: 如果 Worker 正在运行，我们完全信任内存缓存，无视 TTL
                if self._running:
                    return
                # 优先级 2: Worker 没运行，但数据还在 TTL 有效期内
                if time.monotonic() - self._lastrun < self.TTL:
                    return

        # 优先级 3: 从 on_init() 获取
        async with self._fetch_lock:
            if not force:
                # Double-checked lock
                with self._data_lock:
                    if self._running:
                        return
                    if time.monotonic() - self._lastrun < self.TTL:
                        return

            new = await self.on_init_async()
            lastrun = time.monotonic()

            with self._data_lock:
                # 应用更新
                self._lastrun = lastrun
                old = self.data
                self.data = new
                # 编码事件
                updates = list(deep_iter_event(self.TOPIC, old, new))
                if not updates:
                    return
                updates = encode(updates)

            # 锁外广播
            self._broadcast_update(updates)

    async def subscribe(self, topic: BaseTopic):
        await self.fetch_init()

        # --- 原子操作区间 (Lock + No Await) ---
        # 我们必须持有锁，并且在释放锁之前就把快照塞进 channel
        # 或者是利用 Trio 的单线程特性，在 "Add Subscriber" 和 "Send Snapshot" 之间不让 Broadcast 插队
        with self._data_lock:
            # 1. 先入会
            self.subscribers.add(topic)
            # 2. 拍快照 & 编码
            # 此时持有锁，子线程无法修改 data，状态是绝对静止的
            if not self.data:
                return
            full = ResponseEvent(t=self.TOPIC, o='full', v=self.data)
            full = encode(full)

            # 3. 立即发送 (尝试)
            # 只要 send_nowait 成功，Snapshot 就排在了 trio 事件循环的第一位
            # 即使释放锁后 on_event 立即触发 broadcast，Update 也是排在 Snapshot 后面
            try:
                topic.server.send_nowait(full)
                sent_sync = True
            except trio.WouldBlock:
                sent_sync = False

        # 4. 补救发送 (仅当 buffer 满时触发，极罕见)
        # 如果刚才 send_nowait 失败了，我们必须 await。
        # 风险：await 期间，on_event 可能会触发 broadcast，导致 update 插队到 snapshot 前面。
        # 解决：这在逻辑上几乎不可能发生，因为新连接的 channel buffer 应该是空的。
        # 如果真的发生了（buffer size=0?），只能接受。
        if not sent_sync:
            await topic.server.send(full)

    def unsubscribe(self, topic: BaseTopic):
        try:
            self.subscribers.remove(topic)
        except KeyError:
            pass

    async def reinit(self, running: Optional[bool] = None):
        """
        [Trio] 强制重置状态
        1. 无视 TTL，强制执行一次 fetch。
        2. 更新 running 状态。
        """
        # 获取异步锁，防止和并发的 subscribe 冲突，也防止多个 reinit 并发
        async with self._fetch_lock:
            new = await self.on_init_async()
            lastrun = time.monotonic()

            with self._data_lock:
                # 应用更新
                self._lastrun = lastrun
                old = self.data
                self.data = new
                if running is not None:
                    self._running = running
                # 编码事件
                updates = list(deep_iter_event(self.TOPIC, old, new))
                if not updates:
                    return
                updates = encode(updates)

            # 锁外广播
            self._broadcast_update(updates)


class GlobalEventCache(EventCache, metaclass=Singleton):
    pass


class ConfigEventCache(EventCache, metaclass=SingletonNamed):
    def __init__(self, config_name: str):
        self.config_name = config_name
        super().__init__()
