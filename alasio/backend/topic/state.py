from alasio.ext.reactive.rx_trio import async_reactive_source
from alasio.backend.ws.ws_topic import BaseTopic


class ConnState(BaseTopic):
    @async_reactive_source
    def lang(self):
        return 'en-US'

    @async_reactive_source
    def mod_name(self):
        return ''

    @async_reactive_source
    def config_name(self):
        return ''

    @async_reactive_source
    def nav_name(self):
        return ''
