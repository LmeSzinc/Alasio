from alasio.db.table import AlasioTable
from alasio.ext import env
from alasio.ext.singleton import Singleton, SingletonNamed


class AlasioConfigDB(AlasioTable, metaclass=SingletonNamed):
    @classmethod
    def config_file(cls, config_name):
        return env.PROJECT_ROOT / f'config/{config_name}.db'

    def __init__(self, config_name):
        file = self.config_file(config_name)
        super().__init__(file)


class AlasioGuiDB(AlasioTable, metaclass=Singleton):
    def __init__(self):
        file = env.PROJECT_ROOT / f'config/gui.db'
        super().__init__(file)
