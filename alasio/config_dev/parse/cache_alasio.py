from alasio.config.entry.const import ModEntryInfo
from alasio.ext.cache import cached_property
from alasio.ext.singleton import Singleton


class CacheAlasio(metaclass=Singleton):
    @cached_property
    def alasio(self):
        entry = ModEntryInfo.alasio()
        from alasio.config_dev.gen_alasio import Generator
        return Generator(entry)

    def get(self, entry: ModEntryInfo):
        if entry == ModEntryInfo.alasio():
            return False
        return self.alasio
