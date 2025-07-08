from alasio.backend.config.scan import ConfigScan
from alasio.ext.reactive.ws_server import WebsocketTopicServer


class WebsocketServer(WebsocketTopicServer):
    ALL_TOPIC_CLASS = {topic.topic_name(): topic for topic in [
        ConfigScan,
    ]}
