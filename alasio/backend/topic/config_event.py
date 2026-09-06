from alasio.backend.topic._gui_config import GuiConfigSource
from alasio.backend.topic.que import TaskQueueSource


def on_config_event(config_name, event):
    """
    [任意线程] Unified entry of ConfigArg config-save events (sync, thread
    safe). Both the worker recv thread and the RPC success path go through
    here -- there is exactly one entry, so no double-checked or missed
    linkage.

    One call covers every potential consumer of the config:
    - the GUI view sources of the config (every nav of ConfigArg + the
      Dashboard source), routed through the application-level
      GuiConfigSource.dispatch_config;
    - the TaskQueue linkage (TaskQueueSource.on_config_event decides
      whether the scheduler settings changed and refreshes / marks dirty).

    Args:
        config_name (str):
        event (ConfigSetEvent | list[ConfigSetEvent] | dict | list[dict]):
    """
    # 1. GUI view sources: registry snapshot per source class under its
    #    lock, each source filters the event by its own mapping (inbox +
    #    doorbell under its lock)
    GuiConfigSource.dispatch_config(config_name, event)
    # 2. TaskQueue linkage: the source decides whether the scheduler
    #    settings changed; subscribers present -> force refresh on the
    #    Trio thread, no subscriber -> mark dirty
    TaskQueueSource(config_name).on_config_event(event)
