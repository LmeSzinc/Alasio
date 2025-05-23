from alasio.assets_dev.extract import AssetsExtractor
from alasio.assets_dev.parse import AssetModule
from alasio.config.const import Const
from alasio.ext.backport import removeprefix
from alasio.ext.codegen import CodeGen
from alasio.ext.path.calc import subpath_to, uppath


class AssetsExtractorSRC(AssetsExtractor):
    def patch_const(self):
        Const.ASSETS_PATH = 'assets'
        Const.ASSETS_MODULE = 'tasks'
        Const.ASSETS_LANG = dict.fromkeys(['share', 'cn', 'en'])

    def populate(self):
        """
        Populate default attributes
        """
        for module in self.assets:
            for asset in module:
                for image in asset:
                    image.populate_search()
                    # add './' to keep existing format
                    image.path = './' + image.path
                asset.populate_attr_from_first_frame()

    def gen_module(self, gen, module):
        """
        Generate code for a module

        Args:
            gen (CodeGen):
            module (AssetModule):
        """
        # header
        gen.RawImport("""
        from module.base.button import Button, ButtonWrapper
        """)
        gen.CommentCodeGen('dev_tools.button_extract')

        # assets
        for asset in module:
            self.gen_asset(gen, asset)

    def file_to_module(self, file):
        # /assets/share/combat/interact/xxx.png -> combat/interact
        path = uppath(file)
        path = subpath_to(path, self.root.joinpath(Const.ASSETS_PATH))
        path = path.replace('\\', '/')
        for lang in Const.ASSETS_LANG:
            lang = f'{lang}/'
            if path.startswith(lang):
                path = removeprefix(path, lang)
                break
        return path
