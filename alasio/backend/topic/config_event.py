from alasio.backend.reactive.source import ViewportEventSource
from alasio.backend.topic.que import TaskQueueSource


def on_config_event(config_name, event):
    """
    [任意线程] Unified entry of ConfigArg config-save events (sync, thread
    safe). Both the worker recv thread and the RPC success path go through
    here -- there is exactly one entry, so no double-checked or missed
    linkage.

    One call covers every potential consumer of the config:
    - the viewport sources of every nav (ConfigArg) and the Dashboard
      source of the config;
    - the TaskQueue linkage (TaskQueueSource.on_config_event decides
      whether the scheduler settings changed and refreshes / marks dirty).

    Args:
        config_name (str):
        event (ConfigSetEvent | list[ConfigSetEvent] | dict | list[dict]):
    """
    # 1. viewport dispatch: registry snapshot under the registry lock, each
    #    source filters by its own view (inbox + doorbell under its lock)
    ViewportEventSource.dispatch(config_name, event)
    # 2. TaskQueue linkage: the source decides whether the scheduler
    #    settings changed; subscribers present -> force refresh on the
    #    Trio thread, no subscriber -> mark dirty
    TaskQueueSource(config_name).on_config_event(event)
