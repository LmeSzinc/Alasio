from typing import Any, TYPE_CHECKING

import trio

from alasio.backend.worker.event import ConfigEvent
from alasio.backend.reactive.base_rpc import RPCMethod

if TYPE_CHECKING:
    MSGBUS_GLOBAL_SEND: "trio.MemorySendChannel[tuple[str, Any]]"
    MSGBUS_GLOBAL_RECV: "trio.MemoryReceiveChannel[tuple[str, Any]]"
    MSGBUS_CONFIG_SEND: "trio.MemorySendChannel[ConfigEvent]"
    MSGBUS_CONFIG_RECV: "trio.MemoryReceiveChannel[ConfigEvent]"
MSGBUS_GLOBAL_SEND, MSGBUS_GLOBAL_RECV = trio.open_memory_channel(64)
MSGBUS_CONFIG_SEND, MSGBUS_CONFIG_RECV = trio.open_memory_channel(1024)

# A collection of msgbus handlers
MSGBUS_GLOBAL_HANDLERS: "dict[str, list[callable]]" = {}
MSGBUS_CONFIG_HANDLERS: "dict[str, list[callable]]" = {}


class BaseTopic:
    # subclasses should override `topic` and topic name should be unique
    # If topic name is empty, class name will be used
    # The following names are preserved:
    # - "error", the builtin topic to give response to invalid input
    NAME = ''
    # A collection of RPC methods
    # Note that this is auto generated and should be static, don't modify it at runtime
    rpc_methods: "dict[str, RPCMethod]" = {}
    # Whether this topic update with fill event only
    FULL_EVENT_ONLY = False

    @classmethod
    def topic_name(cls):
        if cls.NAME:
            return cls.NAME
        else:
            return cls.__name__

    def __init_subclass__(cls, **kwargs):
        """
        This hook is called when a class inherits from BaseTopic.
        It collects all methods decorated with @rpc, which have been
        pre-processed into RPCMethod objects by the decorator.
        """
        super().__init_subclass__(**kwargs)

        # Create a new registry for this specific subclass, inheriting from parent
        # This prevents child classes from modifying the parent's registry.
        cls.rpc_methods = {}
        cls.msgbus_global_handlers = {}
        cls.msgbus_config_handlers = {}

        for base in cls.__mro__:
            # stop at self
            if base is BaseTopic:
                break
            for name, member in base.__dict__.items():
                if not callable(member):
                    continue
                if hasattr(member, '_rpc_method_instance'):
                    # The decorator has already done the heavy lifting. We just collect the result.
                    cls.rpc_methods[name] = member._rpc_method_instance
                    continue
                # collect msgbus handlers
                topic = getattr(member, '_msgbus_global_topic', None)
                if topic:
                    MSGBUS_GLOBAL_HANDLERS.setdefault(topic, [])
                    handlers = MSGBUS_GLOBAL_HANDLERS[topic]
                    handlers.append((base, member))
                    continue
                topic = getattr(member, '_msgbus_config_topic', None)
                if topic:
                    MSGBUS_CONFIG_HANDLERS.setdefault(topic, [])
                    handlers = MSGBUS_CONFIG_HANDLERS[topic]
                    handlers.append((base, member))
                    continue

    @staticmethod
    async def msgbus_global_asend(topic: str, value):
        """
        Send an event to global msgbus, async method
        """
        event = (topic, value)
        await MSGBUS_GLOBAL_SEND.send(event)

    @staticmethod
    async def msgbus_config_asend(event: ConfigEvent):
        """
        Send an event to config msgbus, async method
        """
        await MSGBUS_CONFIG_SEND.send(event)
