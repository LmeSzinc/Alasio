from alasio.assets.folder import AssetFolder, FolderResponse
from alasio.config.entry.loader import MOD_LOADER


class AssetsManager:
    @classmethod
    def get_data(cls, mod_name, path):
        """
        Args:
            mod_name (str):
            path (str):

        Returns:
            FolderResponse:
        """
        try:
            entry = MOD_LOADER.dict_mod[mod_name].entry
        except KeyError:
            return FolderResponse()
        folder = AssetFolder(entry=entry, path=path)
        return folder.data


if __name__ == '__main__':
    self = AssetsManager()
    print(self.get_data('example_mod', 'assets/combat'))
