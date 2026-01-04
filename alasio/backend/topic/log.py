from collections import deque
from typing import Optional

import msgspec
import trio
from typing_extensions import Self

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.rx_trio import async_reactive_nocache
from alasio.backend.topic.state import ConnState
from alasio.backend.worker.event import ConfigEvent
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.ext.singleton import SingletonNamed
from alasio.logger import logger


class LogCache(metaclass=SingletonNamed):
    """
    高性能日志流管理器：用于将阻塞式子进程 Pipe (Thread) 无缝桥接到异步 WebSocket (Trio)。

    设计目标与核心优化策略：
    ---------------------------------------------------------------------------
    1. 极致的无锁设计 (Lock-Free Concurrency)
       - 利用 CPython 中 `collections.deque` 的 `append`/`popleft` 以及 `len()` 的原子性。
       - 摒弃 `threading.Lock` 和 `trio.Lock`，消除了所有锁竞争和上下文切换开销。
       - 生产者（子线程）与消费者（Trio主线程）互不阻塞，防止 Pipe 堵塞导致子进程死锁。

    2. 自适应背压与批处理 (Adaptive Batching & Doorbell)
       - **门铃机制**：子线程仅在 `len(inbox) == 1` (从空变有) 时调用 `run_sync_soon`。
       - **低负载时**：表现为实时逐条推送，延迟极低。
       - **高负载时**：Inbox 在 Trio 处理间隔内自动积压，Trio 唤醒一次即可处理成百上千条日志，
         自动实现批处理，避免事件循环被数万次微小的回调淹没。

    3. 零空闲开销 (Zero Overhead Idle)
       - 当无 WS 订阅时 (`len(_subscribers) == 0`)，子线程仅执行内存操作 (`cache.append`)。
       - 不产生任何跨线程信号，不占用 Trio 调度资源。

    4. 严格的一致性保证 (Strict Consistency without IDs)
       - 即使不使用序列号 ID，也能保证消息 **不丢失、不重复、不乱序**。
       - **写入顺序 (关键)**：先写入 Inbox (实时流)，后写入 Cache (历史)。
         确保了“凡是进了 Cache 的消息，如果还未分发，必然在 Inbox 中”。
       - **读取顺序 (关键)**：先订阅 Channel，再原子复制 Cache 和 Inbox 快照。
       - **去重算法**：利用 Python 对象内存地址唯一性 (`id/is`)，在订阅瞬间比对
         Snapshot 尾部与 Inbox 头部，通过切片剔除重叠数据。

    5. 内存安全
       - Cache 和 Inbox 均设有 `maxlen`，防止后端处理过慢导致内存无限膨胀 (OOM)。
    """

    @classmethod
    async def get_instance(cls, config_name) -> "Self":
        """
        在异步中调用的方法，获取 LogCache 实例
        """
        instance = cls(config_name)
        if instance.trio_token is None:
            instance.trio_token = trio.lowlevel.current_trio_token()
        return instance

    def __init__(self, config_name):
        self.config_name = config_name
        self.trio_token: "TrioToken | None" = None

        # 1. 历史缓存：所有产生的 log 都会在这里
        # 固定大小 1024, 前端最多显示 1024 行
        self.cache: "deque[ResponseEvent]" = deque(maxlen=1024)

        # 2. 实时中转站：仅在有 WS 订阅时使用
        # 设置 maxlen 是为了防止后端处理不过来时内存爆炸（自动丢弃最旧的实时消息）
        self._inbox: "deque[ResponseEvent]" = deque(maxlen=1024)

        # 3. 订阅者集合：存储 log topic
        self._subscribers: "set[BaseTopic]" = set()

    def on_event(self, event: ConfigEvent):
        """
        [子线程 / 生产者]
        该方法由 pipe 读取线程调用。
        """
        response = ResponseEvent(t=event.t, v=event.v)
        # --- 关键写顺序：先 Inbox，后 Cache ---
        # 这种顺序保证了：凡是进入 Cache 的消息，如果还没发走，必然在 Inbox 里。
        # 这样我们在做去重判断时，永远不会出现“Cache里有，Inbox里没有”导致的漏发。

        # 1. 尝试推入实时流 (Inbox)
        if self._subscribers:
            self._inbox.append(response)

            # 门铃机制 (The Doorbell):
            # 只有当 inbox 从空变为有时，才通知 Trio。
            # 在高频 log 场景下（如每秒 1000 条），Inbox 会积压，导致 len(self._inbox) > 1
            # 此时我们不需要运行 run_sync_soon(_sync_to_trio)，主线程上的 Trio 事件循环会直接拿取 self._inbox。
            # 这实现了天然的“自动批处理”，极大节省了跨线程通讯开销。
            if len(self._inbox) == 1:
                try:
                    self.trio_token.run_sync_soon(self._sync_to_trio)
                except trio.RunFinishedError:
                    pass  # 事件循环已关闭
                except AttributeError:
                    logger.warning('Failed to broadcast event, trio_token not initialized')

        # 2. 推入历史缓存 (Cache)
        # deque.append 在 CPython 中是线程安全的原子操作
        self.cache.append(response)

    def _sync_to_trio(self):
        """
        [Trio 主线程 / 消费者]
        由 run_sync_soon 调度执行。负责清空 Inbox 并分发。
        """
        if not self._inbox:
            return

        # 1. 原子化取走 Inbox 所有内容
        # 即使在 pop 过程中子线程继续 append，逻辑依然安全
        batch = []
        while True:
            try:
                batch.append(self._inbox.popleft())
            except IndexError:
                # maybe raise condition that _inbox is empty
                break
            if not self._inbox:
                break

        # 2. 广播给所有订阅者
        if batch:
            for topic in self._subscribers:
                try:
                    # 使用 send_nowait 防止某个订阅者阻塞导致整个后端卡顿
                    # 如果订阅者处理太慢，send_nowait 会抛出 WouldBlock (或丢弃，取决于 channel 类型)
                    topic.server.send_nowait(batch)
                except trio.WouldBlock:
                    pass

    def subscribe(self, topic: BaseTopic):
        """
        [Trio 主线程]
        核心订阅逻辑：将 Log 流转发给指定的 WS topic。
        包含：无锁去重、快照发送、实时流转发。
        """
        # --- 原子操作区间 (Atomic Block) 开始 ---
        # 在 Trio 中，只要没有 await，以下代码是不可中断的。
        # 子线程虽然在并行运行，但 _sync_to_trio 不会插队。

        # 1. 立即订阅：保证“未来”的数据不丢失
        # 从这一刻起，_sync_to_trio 会开始向 internal_send 灌入数据
        self._subscribers.add(topic)

        # 2. 捕获定格画面 (Atomic Copy)
        # 即使子线程在疯狂写入，list() 复制是瞬间完成的。
        # 关键读顺序：先 Cache，后 Inbox。
        # 配合子线程的“先 Inbox 后 Cache”，确保了我们覆盖了所有时间线。
        snapshot = list(self.cache)  # 历史
        pending_copy = list(self._inbox)  # 已经在路上但还没分发的实时消息

        # 3. 极速去重逻辑 (Deduplication)
        # 目标：如果 inbox 里的消息已经出现在 snapshot 尾部，通过切片把它们从 snapshot 里去掉。
        # 这样前端先收到 snapshot，再收到 inbox 里的内容，拼接起来不仅严丝合缝，而且不重复。

        if pending_copy and snapshot:
            first_pending_obj = pending_copy[0]

            # 限制查找深度，防止极端情况下的性能损耗
            limit = min(len(snapshot), len(pending_copy) + 20)

            # 使用 reversed() 迭代器，零内存拷贝
            for i, event in enumerate(reversed(snapshot)):
                # 超过限制还没找到，说明没有重叠（或者重叠部分已被覆盖），停止查找
                if i >= limit:
                    break

                # 使用 is 进行极速指针比对
                if event is first_pending_obj:
                    # 找到了！
                    # i 是倒序索引 (0 代表最后一条, 1 代表倒数第二条...)
                    # 对应的切片截止点正是 len(snapshot) - 1 - i
                    # 但因为切片是左闭右开，所以我们要切到 len(snapshot) - i 吗？
                    # 不，切片 snapshot[:-i] 等价于 snapshot[:len-i]
                    # 如果 i=0 (最后一条重叠)，我们要切掉最后一条，即 [:len-1]
                    # 稍微推算一下：
                    # 倒序 i=0 -> 是 snapshot[-1] -> 需要保留 snapshot[:-1] -> cut_index = len - 1
                    # 倒序 i=1 -> 是 snapshot[-2] -> 需要保留 snapshot[:-2] -> cut_index = len - 2

                    cut_index = len(snapshot) - 1 - i
                    snapshot = snapshot[:cut_index]
                    break

        # --- 原子操作区间 结束 ---

        # 4. 发送快照
        # 合并全部行到一个 full 事件
        # 不论有没有snapshot，都要发送full事件来显式通知前端
        event = ResponseEvent(t=topic.topic_name(), o='full', v=[e.v for e in snapshot])
        try:
            # 使用 send_nowait 确保这一步不会挂起 (yield)。
            # 如果使用 await send()，Trio 可能会在等待期间切换去执行 _sync_to_trio，
            # 导致 target_ws_channel 在收到 "full" 之前先收到了 "add"。
            # 只要 target_ws_channel 的 buffer 足够 (例如 > 1)，这里就不会报错。
            topic.server.send_nowait(event)
        except trio.WouldBlock:
            # 极其罕见：连接刚建立 channel 就满了？
            pass

        # 5. 保持订阅状态
        # 接下来会持续推送，直到调用 unsubscribe

    def unsubscribe(self, topic: BaseTopic):
        try:
            self._subscribers.remove(topic)
        except KeyError:
            pass
        # 自动清理 inbox (如果没人看了)
        if not self._subscribers:
            self._inbox.clear()


class LogData(msgspec.Struct, omit_defaults=True):
    # timestamp in seconds, in UTC
    t: float
    # logging level in uppercase:
    #   DEBUG, INFO, WARNING, ERROR, CRITICAL, and maybe custom level
    l: str
    # log message, might contain "\n"
    m: str
    # exception track back if error occurs, might contain "\n"
    e: Optional[str] = None
    # raw tag, if r=1 this is a raw message
    # meaning don't show time and level, log message is pre-formatted, show it directly
    r: Optional[int] = None


class Log(BaseTopic):
    cache: "LogCache | None" = None

    @async_reactive_nocache
    async def data(self):
        """
        Returns:
            list[dict]: list of structlog data
        """
        # reactive dependency changed, unsubscribe last cache
        if self.cache is not None:
            self.cache.unsubscribe(self)

        state = ConnState(self.conn_id, self.server)
        config_name = await state.config_name
        if not config_name:
            # empty logs if config_name is empty
            event = ResponseEvent(t=self.topic_name(), o='full', v=[])
            await self.server.send(event)
            return

        cache = await LogCache.get_instance(config_name)
        self.cache = cache
        cache.subscribe(self)

    async def op_sub(self):
        """
        LogCache.subscribe already send, no need to send here
        """
        await self.data

    async def op_unsub(self):
        # topic unsubscribed, unsubscribe cache too
        if self.cache is not None:
            self.cache.unsubscribe(self)

    async def reactive_callback(self, name, old, new):
        # also no reactive callback
        pass
