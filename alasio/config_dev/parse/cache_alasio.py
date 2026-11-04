from alasio.config.entry.mod_entry import ModEntry
from alasio.ext.cache import cached_property
from alasio.ext.singleton import Singleton


class CacheAlasio(metaclass=Singleton):
    @cached_property
    def alasio(self):
        entry = ModEntry.alasio
        from alasio.config_dev.gen_alasio import Generator
        return Generator(entry)

    def get(self, entry: ModEntry):
        """
        Get the generator of the library, or False when the entry is the
        library itself.

        Args:
            entry (ModEntry): Entry of the mod being generated

        Returns:
            Generator | bool: The generator of the library, False for the
                library itself
        """
        # The entry of the library is a singleton, the compare stays an identity
        # check however the caller built its own handle
        if entry.root == ModEntry.alasio.root:
            return False
        return self.alasio
