from alasio.backend.topic.scan import ConfigScan
from alasio.backend.topic.state import ConnState
from alasio.backend.ws.ws_server import WebsocketTopicServer


class WebsocketServer(WebsocketTopicServer):
    ALL_TOPIC_CLASS = {topic.topic_name(): topic for topic in [
        ConfigScan,
        ConnState,
    ]}
