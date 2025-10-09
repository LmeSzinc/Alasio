from alasio.assets.folder import AssetFolder, FolderResponse
from alasio.config.entry.loader import MOD_LOADER
from alasio.ext.path.validate import validate_resolve_filepath


class AssetsManager:
    @classmethod
    def get_folder_manager(cls, mod_name, path):
        """
        Args:
            mod_name (str):
            path (str): Relative path from project root to assets folder. e.g. assets/combat

        Returns:
            AssetFolder:

        Raises:
            ValueError: If mod_name invalid or path invalid
        """
        try:
            entry = MOD_LOADER.dict_mod[mod_name].entry
        except KeyError:
            raise ValueError(f'No such mod: "{mod_name}"')

        validate_resolve_filepath(entry.root, path)
        obj = AssetFolder(entry=entry, path=path)
        return obj

    @classmethod
    def get_data(cls, mod_name, path):
        """
        Args:
            mod_name (str):
            path (str): Relative path from project root to assets folder. e.g. assets/combat

        Returns:
            FolderResponse:

        Raises:
            ValueError: If mod_name invalid or path invalid
        """
        manager = cls.get_folder_manager(mod_name, path)
        return manager.data


if __name__ == '__main__':
    self = AssetsManager()
    print(self.get_folder_manager('example_mod', 'assets').data)
