def on_msgbus_global_event(topic):
    """
    A decorator that mark method as msgbus event handler.
    Method will be called when event with the same topic appears.
    Note that method must be async, because msgbus is async.

    Examples:
        class ConfigScan(BaseTopic):
            @on_msgbus_global_event('ConfigScan')
            async def broadcast(self, value):
                pass
    """

    def register(func):
        func._msgbus_global_topic = topic
        return func

    return register


def on_msgbus_config_event(topic):
    """
    A decorator that mark method as config-specific event handler.
    Method will be called when event with the same topic appears.
    Note that method must be async, because msgbus is async.

    Examples:
        class ConfigScan(BaseTopic):
            @on_msgbus_config_event('ConfigScan')
            async def broadcast(self, value):
                pass
    """

    def register(func):
        func._msgbus_config_topic = topic
        return func

    return register
