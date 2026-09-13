import time

from alasio.backend.reactive.event import ResponseEvent
from alasio.backend.reactive.source import GlobalSource, ResidentCache
from alasio.backend.ws.ws_topic import BaseTopic


class RestartSource(GlobalSource, ResidentCache):
    """
    Phase of the graceful backend restart: data = {phase, update} while a
    restart is in progress, empty otherwise.

    Event protocol: on_event(phase) -- '' clears the data (no restart in
    progress), any other value sets the whole data. Worker states are NOT
    duplicated here: the Worker topic is the single source of truth for every
    worker, this topic only tells whether a restart is in progress and in
    which stage (the frontend reads the per-worker progress from Worker).

    Phases:
    - 'stopping': waiting for the workers to stop gracefully;
    - 'shutting-down': all workers stopped, the backend is about to exit;
    - 'resuming': the new backend is starting the recorded workers;
    - 'done': the resume queue was processed (transient, usually only seen by
      a frontend that reconnects fast).
    """
    TOPIC_NAME = 'Restart'

    def _apply(self, event):
        """
        [锁内] phase -> data; '' clears it (no restart in progress)

        Returns:
            bool: If data changed
        """
        if not event:
            if not self.data:
                return False
            self.data = {}
            return True
        data = {'phase': event, 'update': time.time()}
        if self.data == data:
            return False
        self.data = data
        return True

    def _convert(self, event):
        """
        [锁内] Whole-data set: the payload references the data dict, which is
        replaced wholesale by later events (never mutated in place), so the
        deferred encoding outside the lock is safe

        Returns:
            ResponseEvent:
        """
        return ResponseEvent(t=self.TOPIC_NAME, o='full', v=self.data)


class Restart(BaseTopic):
    TOPIC_NAME = 'Restart'

    async def get_source(self):
        """
        Restart phases flow entirely through events, there is no full-data
        source to refresh on subscribe
        """
        return RestartSource()
