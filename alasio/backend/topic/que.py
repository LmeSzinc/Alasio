from typing import List, Optional, TypedDict

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.event_cache import ConfigEventCache
from alasio.backend.reactive.rx_trio import async_reactive_nocache
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.entry.loader import MOD_LOADER
from alasio.config.entry.mod import Task


class TaskQueueData(TypedDict):
    running: Optional[Task]
    pending: List[Task]
    waiting: List[Task]


class TaskQueueSource(ConfigEventCache):
    TOPIC = 'TaskQueue'
    data: TaskQueueData

    def on_init(self) -> TaskQueueData:
        # access cache directly, no rescan
        configs = ConfigScanSource().data
        try:
            info = configs[self.config_name]
        except KeyError:
            return {'running': None, 'pending': [], 'waiting': []}
        try:
            mod = MOD_LOADER.dict_mod[info.mod]
        except KeyError:
            return {'running': None, 'pending': [], 'waiting': []}

        pending_task, waiting_task = mod.get_task_schedule(self.config_name)
        return {'running': None, 'pending': pending_task, 'waiting': waiting_task}


class TaskQueue(BaseTopic):
    cache: "TaskQueueSource | None" = None

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
            # event = ResponseEvent(t=self.topic_name(), o='full', v={})
            # await self.server.send(event)
            return

        cache = TaskQueueSource(config_name)
        self.cache = cache
        await cache.subscribe(self)

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
