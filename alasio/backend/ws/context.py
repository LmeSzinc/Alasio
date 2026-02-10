import trio

from alasio.ext.singleton import Singleton


class GlobalContext(metaclass=Singleton):
    def __init__(self):
        # will be injected on lifespan start
        self.global_nursery: trio.Nursery = None
        self.trio_token: trio._core.TrioToken = None


GLOBAL_CONTEXT = GlobalContext()
