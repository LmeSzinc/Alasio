from typing import Literal

from trio import current_time

from alasio.backend.reactive.background import BackgroundTask
from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import RpcValueError
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.worker.event import CommandEvent
from alasio.backend.worker.manager import WORKER_STATUS
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.table.scan import validate_config_name
from alasio.ext.singleton import SingletonNamed
from alasio.logger import logger

PREVIEW_AVAILABLE = ['running', 'scheduler-stopping']
PREVIEW_SPEED = Literal['normal', 'realtime']


class PreviewTask(BackgroundTask, metaclass=SingletonNamed):
    """
    管理预览图片发送任务

    ## 消息通信
    后端发送 CommandEvent(c='preview') 给 worker 请求预览图片，worker 在每次截图之后检查后端是否请求图片，
    如果是 则发送预览图片，WorkerManager 收到预览图片后转发给 on_preview()
    多次发送预览请求不会收到多张图片，因为 worker 只是在有新截图的时候发送那一张新截图。

    ## 前端调用
    前端获取预览图片需要首先订阅 Preview，订阅主题不会发送图片。前端调用RPC方法 preview_start() 后端才会开始发送图片，
    preview_start() 会进一步调用 PreviewTask.subscribe()
    注意，前端与worker没有确定的关系，意味着可能存在多个前端正在查看同一个worker，被查看的worker不一定正在运行，
    可能存在worker没有前端在看，前端在查看一个worker的时候可能切换去查看另一个worker，前端可能切换查看速度。

    ## 显示速度
    显示速度分两档，normal 和 realtime。
    normal 是每两秒发送一张预览图片给前端。两秒是平均而言的，真实发送间隔可能在两秒上下浮动，取决于worker获取截图的时刻。
    比如 worker 以 0.3s 的间隔截图，那么真实发送时刻是 0.0s, 2.1s, 4.2s, 6.0s, 8.1s, ...
    如果 worker 的截图耗时超过两秒，发送间隔也将超过两秒。
    realtime 是发送 worker 的实时截图。
    但 realtime 并不意味着可以在前端看到实时的游戏画面，只是 worker 会把它获得的每一张截图都发出。

    ## 开始订阅
    我们使用 self._subscribers 储存每个订阅者要求的显示速度，取其中最快的作为请求 worker 的速度，储存在 self._speed，
    当有新的订阅者时，我们立刻请求一张新的截图，并把下一次发送安排在两秒后，这样可以同步所有正在查看这个worker的前端。
    如果有人订阅一个worker但是worker并不在运行，那么跳过请求截图，标记为 _trigger_on_running。
    当worker状态发生改变的时候，WorkerManager 会回调 on_worker_state()，如果worker处于 PREVIEW_AVAILABLE 并且有启动标记，
    那么触发一次任务，后台任务会立刻请求一张新截图，然后每两秒再请求一次。

    ## 更改速度
    | 订阅者速度(旧) | worker速度(旧) | 订阅者速度(新) | worker速度(新) |
    | -------------- | -------------- | -------------- | -------------- |
    1. 订阅者变快，并且是唯一订阅者或者有其他慢速订阅者，订阅者变快了worker也变快了，立刻触发一次task
    | 慢             | 慢             | 快             | 快             |
    2. 订阅者变快，有其他快速订阅者。其实不需要操作，收到截图的时候会额外转发给当前订阅者，但为了语义明确还是触发一次task
    | 慢             | 快             | 快             | 快             |
    3. 订阅者变慢，并且是唯一订阅者或者有其他慢速订阅者，worker变慢，无需触发task，只需等待下一次task的重新执行
    | 快             | 快             | 慢             | 慢             |
    4. 订阅者变慢，有其他快速订阅者，worker速度不变。无需触发task，转发截图的时候会按慢速tick转发给当前订阅者
    | 快             | 快             | 慢             | 快             |
    因此，新增订阅和变快的时候触发task，变慢的时候不触发

    ## 截图转发
    当worker发送预览的时候，WorkerManager会转发到 PreviewTask.on_preview()
    on_preview() 根据每个订阅者要求的速度和上一次发送时间进行转发。
    如果请求 worker 的速度是 realtime，那么在收到截图的时候再次请求截图，形成快速无限请求。

    ## 停止订阅
    前端调用RPC方法preview_stop() 或者取消订阅Preview，调用 PreviewTask.unsubscribe()
    如果没有订阅者了，停止task循环
    """

    def __init__(self, config_name):
        super().__init__()
        self.config_name = config_name
        # Avoid circular import
        from alasio.backend.topic._worker import BACKEND_WORKER_MANAGER
        self._manager = BACKEND_WORKER_MANAGER
        self._nursery = GLOBAL_CONTEXT.global_nursery

        # preview speed of each subscribers
        # key: topic, value: speed, 'normal' or 'realtime'
        self._subscribers: "dict[BaseTopic, PREVIEW_SPEED]" = {}
        # last send of normal speed, a sync tick among all subscribers
        self._normal_lastsend = -10000
        # preview speed of worker
        self._speed: PREVIEW_SPEED = 'normal'
        self._trigger_on_running = False

    def _get_speed(self) -> PREVIEW_SPEED:
        for speed in self._subscribers.values():
            if speed == 'realtime':
                return 'realtime'
        return 'normal'

    def subscribe(self, topic: BaseTopic, speed: PREVIEW_SPEED):
        old_sub_speed = self._subscribers.get(topic)
        if old_sub_speed == speed:
            # nothing changed
            return
        print(old_sub_speed, speed)
        speed_decrease = (old_sub_speed == 'realtime') and (speed == 'normal')
        self._subscribers[topic] = speed
        self._speed = self._get_speed()

        # clear lastsend
        # - current subscriber will receive the next preview
        # - rest of subscribers will also receive next preview for sync
        if not speed_decrease:
            self._normal_lastsend = -10000
        # worker idle, skip trigger and record as _trigger_on_running
        worker = self._manager.state.get(self.config_name)
        if worker is None:
            self._trigger_on_running = True
            return
        if not worker.status in PREVIEW_AVAILABLE:
            self._trigger_on_running = True
            return
        # trigger
        if not speed_decrease:
            self.task_trigger(self._nursery)

    def unsubscribe(self, topic: BaseTopic):
        self._subscribers.pop(topic, None)
        self._speed = self._get_speed()
        # stop task if no subscribers
        if not self._subscribers:
            self._normal_lastsend = -10000
            self._trigger_on_running = False
            self.task_stop()

    async def task_run(self):
        worker = self._manager.state.get(self.config_name)
        # worker should be running when task_run() is called
        # if race condition happens, worker is no longer running, re-enable _trigger_on_running
        if worker is None:
            self._trigger_on_running = True
            return
        if worker.status in PREVIEW_AVAILABLE:
            self._trigger_on_running = False
        else:
            self._trigger_on_running = True
            return
        # send command
        command = CommandEvent(c='preview')
        worker.send_command(command)

    def on_worker_status(self, status: WORKER_STATUS):
        """
        Callback function when worker state changed
        """
        if not self._subscribers:
            self.task_stop()
            return
        if status not in PREVIEW_AVAILABLE:
            self.task_stop()
            return
        # request to send preview when start running
        if self._trigger_on_running:
            # set False first, task_trigger may re-enable _trigger_on_running
            self._trigger_on_running = False
            self.task_trigger(self._nursery)

    def on_preview(self, preview: bytes):
        """
        Callback function when worker sends a preview
        """
        _subscribers = self._subscribers
        _normal_lastsend = self._normal_lastsend
        _speed = self._speed

        # broadcast
        now = current_time()
        normal_outdated = (now - _normal_lastsend) >= self.recurrence
        if normal_outdated:
            self._normal_lastsend = now
        for topic, topic_speed in _subscribers.items():
            # speed='realtime', send directly
            if topic_speed == 'realtime':
                topic.server.send_lossy(preview)
                continue
            # speed='normal', last send too far
            if normal_outdated:
                topic.server.send_lossy(preview)
                continue
            # speed='normal', just recently send, skip
            continue

        # request another preview when receiving a preview, so previews will be infinitely sent
        if _subscribers and _speed == 'realtime':
            self._last_finish_time = -10000
            self.task_trigger(self._nursery)


class Preview(BaseTopic):
    cache: "PreviewTask | None" = None

    async def getdata(self):
        # no full data
        return {}

    @rpc
    async def preview_start(self, name: str, speed: PREVIEW_SPEED):
        # check if name is a validate filename
        error = validate_config_name(name)
        if error:
            raise RpcValueError(error)
        # get current configs
        data = ConfigScanSource().data
        try:
            _ = data[name]
        except KeyError:
            raise RpcValueError(f'No such config: "{name}"')

        old = self.cache
        if old is None:
            # new subscribe
            new = PreviewTask(name)
            self.cache = new
            new.subscribe(self, speed)
        elif old.config_name == name:
            # same config, change speed
            old.subscribe(self, speed)
        else:
            # change config
            old.unsubscribe(self)
            new = PreviewTask(name)
            self.cache = new
            new.subscribe(self, speed)

    @rpc
    async def preview_stop(self):
        if self.cache is not None:
            self.cache.unsubscribe(self)
            self.cache = None

    async def op_unsub(self):
        logger.info('op_unsub')
        if self.cache is not None:
            self.cache.unsubscribe(self)
            self.cache = None
