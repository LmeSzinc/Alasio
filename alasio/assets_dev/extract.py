import re

from alasio.assets_dev.parse import AssetAll, AssetImage, AssetModule, AssetMultilang
from alasio.base.image.imfile import image_fixup
from alasio.config.const import Const
from alasio.ext.cache import cached_property, del_cached_property
from alasio.ext.codegen import CodeGen
from alasio.ext.file.watchdog import PathEvent, Watchdog
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_remove
from alasio.ext.path.calc import get_rootstem, get_suffix, subpath_to, uppath
from alasio.ext.path.iter import iter_files
from alasio.logger import logger

REGEX_ASSETS_NAME = re.compile(r'^[a-zA-Z0-9_]+$')


class AssetsExtractor:
    def __init__(self, root):
        """
        Args:
            root (PathStr | str): Absolute path to project root
        """
        self.root: "PathStr" = PathStr.new(root)

    @staticmethod
    def is_assets_file(file):
        """
        Check if a file is a valid asset file
        """
        # check file extension
        suffix = get_suffix(file)
        if suffix not in Const.ASSETS_EXT:
            return False

        name = get_rootstem(file)
        # emulator screenshots is not allowed
        # assets should have a name with meaning, not just random timestamp
        if name.startswith('Screenshot'):
            # LDPlayer: Screenshot_20250520-212905.png
            return False
        if name.startswith('MuMu'):
            # MuMu12: MuMu12-20250521-072111.png
            # MuMu6: MuMu20190305233638.png
            return False
        if name.startswith('NemuPlayer'):
            # NemuPlayer: NemuPlayer 2019-03-04 18-48-16-52.png
            return False

        # check if assets name can convert to python variable name
        if '-' in name:
            return False
        if ' ' in name:
            return False
        try:
            if name[0].isdigit():
                return False
        except IndexError:
            # empty name
            return False

        # check name format
        if REGEX_ASSETS_NAME.match(name):
            return True
        else:
            return False

    def patch_const(self):
        pass

    @cached_property
    def watchdog(self) -> Watchdog:
        self.patch_const()
        path = self.root.joinpath(Const.ASSETS_PATH)
        paths = [path.joinpath(lang) for lang in Const.ASSETS_LANG]
        return Watchdog(paths)

    @cached_property
    def assets(self) -> AssetAll:
        """
        Build assets structure from local files
        """
        self.patch_const()
        path = self.root.joinpath(Const.ASSETS_PATH)

        def it():
            for lang in Const.ASSETS_LANG:
                server = path.joinpath(lang)
                AssetImage.REPO_ROOT = self.root
                AssetImage.LANG_ROOT = server
                for file in iter_files(server, ext='.png', recursive=True):
                    if self.is_assets_file(file):
                        file = AssetImage(file)
                        file.lang = lang
                        yield file

        return AssetAll(it)

    def populate(self):
        """
        Populate default attributes
        """
        for module in self.assets:
            for asset in module:
                for image in asset:
                    image.populate_search()
                    image.path = image.path
                asset.populate_attr_from_first_frame()

    def gen_asset(self, gen, asset):
        """
        Generate code for an asset

        Args:
            gen (CodeGen):
            asset (AssetMultilang):
        """
        valid_lang = [lang for lang in Const.ASSETS_LANG if lang != 'share']
        with gen.Object(name=asset.asset, cls='ButtonWrapper'):
            gen.Var(name='name', value=asset.asset)
            # if assets has shared images, language specific images are optional
            # if assets don't have, all language specific images should present,
            #   otherwise we leave a None there to notify maintainers
            if asset.has_share:
                list_lang = asset.dict_lang_frame
            else:
                list_lang = valid_lang
            # iter language
            for lang in list_lang:
                dict_frame = asset.dict_lang_frame.get(lang, {})
                count = len(dict_frame)
                if count == 1:
                    # only one frame
                    for frame in dict_frame.values():
                        with gen.Object(name=lang, cls='Button'):
                            gen.Var(name='file', value=frame.path)
                            gen.Var(name='area', value=frame.area)
                            gen.Var(name='search', value=frame.search)
                            gen.Var(name='color', value=frame.color)
                            gen.Var(name='button', value=frame.button)
                elif count > 1:
                    # list of frames
                    with gen.List(name=lang):
                        for frame in dict_frame.values():
                            with gen.Object(cls='Button'):
                                gen.Var(name='file', value=frame.path)
                                gen.Var(name='area', value=frame.area)
                                gen.Var(name='search', value=frame.search)
                                gen.Var(name='color', value=frame.color)
                                gen.Var(name='button', value=frame.button)
                else:
                    # leaving None
                    gen.Var(name=lang, value=None)

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

    def gen_output(self, module):
        """
        Args:
            module (AssetModule):

        Returns:
            PathStr: output file of module
        """
        # tasks/combat/assets/assets_support.py
        file = f'assets_{module.module.replace("/", "_")}.py'
        module, _, _ = module.module.partition('/')
        file = self.root.joinpath(f'{Const.ASSETS_MODULE}/{module}/assets/{file}')
        return file

    @staticmethod
    def remove_old_files(files):
        """
        remove old files from the output folder,
        leaving auto-generated files and __init__.py only

        Args:
            files (set[PathStr]): all output files
        """
        # get all output folders
        folders = set()
        for file in files:
            folder = file.uppath()
            folders.add(folder)
        # iter existing files
        for folder in folders:
            for file in iter_files(folder, ext='.py', recursive=False):
                if file in files:
                    continue
                if file.endswith('__init__.py'):
                    continue
                # remove file
                logger.info(f'Remove file: {file}')
                atomic_remove(file)

    def generate(self, modules=None):
        """
        Generate all assets

        Args:
            modules (list[str] | set[str] | str): To generate given modules only
        """
        if modules is None:
            logger.info('Assets generate')
        else:
            if isinstance(modules, str):
                modules = {modules,}
            if not isinstance(modules, set):
                modules = set(modules)
            logger.info(f'Assets generate, modules={modules}')

        # build assets structure
        del_cached_property(self, 'assets')
        _ = self.assets
        # read all images
        self.assets.load(modules)
        # populate
        self.populate()

        # iter modules
        files = set()
        for module in self.assets:
            # record output
            file = self.gen_output(module)
            files.add(file)

            if modules is not None:
                if module.module not in modules:
                    continue
            # generate code
            gen = CodeGen()
            self.gen_module(gen, module)
            # write
            write = gen.write(file, skip_same=True)
            if write:
                logger.info(f'Write file {file}')

        self.remove_old_files(files)

        # cleanup
        del_cached_property(self, 'assets')

    def file_to_module(self, file):
        # /assets/combat/interact/xxx.png -> combat/interact
        path = uppath(file)
        path = subpath_to(path, self.root.joinpath(Const.ASSETS_PATH))
        path = path.replace('\\', '/')
        return path

    def generate_on_change(self, events):
        """
        Args:
            events (list[PathEvent]):
        """
        self.patch_const()

        modules = set()
        for event in events:
            # must be an asset
            file = event.path
            if not self.is_assets_file(file):
                continue
            # try to find which module it belongs to
            # /assets/share/combat/interact -> combat/interact
            module = self.file_to_module(file)
            modules.add(module)

        if not modules:
            return

        self.generate(modules)

    def image_fixup(self, event):
        """
        Args:
            event (PathEvent):

        Returns:
            bool: If file changed
        """
        # must be an asset
        file = event.path
        if not self.is_assets_file(file):
            return False
        # fixup
        if image_fixup(file):
            logger.info(f'Fixup image: {file}')
            return True
        else:
            return False

    def watch_files(self, interval=2):
        """
        Watch files and generate on change
        """
        events = self.watchdog.watch_files(include_existing=False, interval=interval)
        for event in events:
            logger.info(event)
            if self.image_fixup(event):
                self.watchdog.mark_modified(event)
        self.generate_on_change(events)
