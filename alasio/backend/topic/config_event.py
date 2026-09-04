import trio

from alasio.backend.reactive.source import ViewportEventSource
from alasio.backend.topic.que import TaskQueueSource
from alasio.backend.ws.context import GLOBAL_CONTEXT
from alasio.logger import logger


def on_config_event(config_name, event):
    """
    [任意线程] Unified entry of ConfigArg config-save events (sync, thread
    safe). Both the worker recv thread and the RPC success path go through
    here -- there is exactly one entry, so no double-checked or missed
    linkage.

    One call covers every potential consumer of the config:
    - the viewport sources of every nav (ConfigArg) and the Dashboard
      source of the config;
    - the TaskQueue linkage (recompute the task queue when
      Scheduler.Enable / NextRun changed).

    Args:
        config_name (str):
        event (ConfigSetEvent | list[ConfigSetEvent] | dict | list[dict]):
    """
    # 1. viewport dispatch: registry snapshot under the registry lock, each
    #    source filters by its own view (inbox + doorbell under its lock)
    ViewportEventSource.dispatch(config_name, event)
    # 2. TaskQueue linkage: synchronous in-memory check + fire-and-forget
    #    delivery of the async reinit
    if _need_task_queue_reinit(event):
        _schedule_task_queue_reinit(config_name)


def _need_task_queue_reinit(responses):
    """
    Synchronous check: does the payload touch Scheduler.Enable / NextRun?
    Handles ConfigSetEvent and dict payloads, single or list.

    Args:
        responses (ConfigSetEvent | list[ConfigSetEvent] | dict | list[dict]):

    Returns:
        bool:
    """
    if not isinstance(responses, list):
        responses = [responses]
    for resp in responses:
        if resp is None:
            continue
        # worker payloads are dicts (decoded from bytes)
        if type(resp) is dict:
            try:
                group = resp['group']
                arg = resp['arg']
            except KeyError:
                continue
        else:
            group = resp.group
            arg = resp.arg
        if group == 'Scheduler' and (arg == 'Enable' or arg == 'NextRun'):
            return True
    return False


def _schedule_task_queue_reinit(config_name):
    """
    [任意线程] Deliver the async reinit to the Trio thread
    (fire-and-forget). Debounced: consecutive hits while a reinit is
    pending / running merge into it.
    """
    source = TaskQueueSource(config_name)
    if not source._reinit_mark():
        # a forced reinit is already pending / running: merge this hit
        return
    try:
        GLOBAL_CONTEXT.trio_token.run_sync_soon(_spawn_task_queue_reinit, config_name)
    except trio.RunFinishedError:
        pass  # event loop already shut down


def _spawn_task_queue_reinit(config_name):
    """
    [Trio 线程, run_sync_soon callback] Launch the linkage coroutine.
    start_soon inside a run_sync_soon callback is legal on trio 0.27
    (measured; same shape as PreviewTask.on_preview).
    """
    try:
        GLOBAL_CONTEXT.global_nursery.start_soon(_task_queue_reinit, config_name)
    except RuntimeError:
        pass  # nursery already ended (shutdown race)


async def _task_queue_reinit(config_name):
    """
    [Trio task] Linkage body: force recompute the task queue and broadcast.
    Coroutine exceptions bubble up to the lifespan root nursery and would
    crash the backend: catch and log them here.
    """
    try:
        await TaskQueueSource(config_name).reinit(force=True)
    except Exception as e:
        logger.exception(e)
    finally:
        TaskQueueSource(config_name)._reinit_clear()
