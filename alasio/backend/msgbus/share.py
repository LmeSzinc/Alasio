from typing import Any, Tuple

from msgspec import Struct


class ConfigEvent(Struct):
    """
    Message from worker to backend.
    Backend will broadcast to websocket connections that subscribed this topic and key.
    """
    # topic_name
    t: str
    # config_name
    c: str
    # Operation is always set
    # Key path of config
    # Usually to be (task, group, arg), might also be any custom key path
    k: Tuple = ()
    # Value.
    # value may be omitted, if so, value is None
    # if operation is "del", value will be omitted
    v: Any = None
