from collections import defaultdict
from dataclasses import dataclass
from typing import Iterator, List, Tuple

from alasio.base.image.color import get_color
from alasio.base.image.draw import get_bbox
from alasio.base.image.imfile import image_fixup, image_load, image_size
from alasio.config.const import Const
from alasio.ext.area.area import Area
from alasio.ext.area.slist import Slist
from alasio.ext.cache import cached_property
from alasio.ext.path.calc import subpath_to
from alasio.ext.pool import WORKER_POOL
from alasio.logger import logger


class AssetImage:
    REPO_ROOT = ''
    LANG_ROOT = ''

    def __init__(self, file):
        """
        Args:
            file (str): Absolute filepath
        """
        self.file = file
        self.path = subpath_to(file, AssetImage.REPO_ROOT).replace('\\', '/')

        # parse file like:
        # /path/to/BATTLE_PREPARATION.png
        # /path/to/BATTLE_PREPARATION.cn.2.png
        # /path/to/BATTLE_PREPARATION.area.png
        # /path/to/BATTLE_PREPARATION.cn.2.search.png
        filepath = subpath_to(file, AssetImage.LANG_ROOT).replace('\\', '/')
        module, _, filename = filepath.rpartition('/')
        self.module = module.replace('\\', '/')
        stem, _, suffix = filename.rpartition('.')
        self.asset, _, suffixes = stem.partition('.')
        self.lang = 'share'
        self.frame = 0
        self.attr = ''
        for suffix in suffixes.lower().split('.'):
            if suffix in Const.ASSETS_EXT:
                continue
            if suffix in Const.ASSETS_ATTR:
                self.attr = suffix
                continue
            if suffix in Const.ASSETS_LANG:
                self.lang = suffix
                continue
            if suffix.isdigit():
                self.frame = int(suffix)
                continue

        self.valid = False
        self.bbox = ()
        self.mean = ()

    def __str__(self):
        return f'{self.__class__.__name__}(file={self.file}, valid={self.valid})'

    def load(self):
        """
        Parse image file, get bbox and mean
        """
        image = image_load(self.file)
        # must match resolution
        size = image_size(image)
        resolution = Const.ASSETS_RESOLUTION
        if not Const.ASSETS_TEMPLATE_PREFIX and size != resolution:
            logger.warning(f'{self.file} has wrong resolution: {size}')
            self.valid = False
            return

        bbox = get_bbox(image)
        # must be an asset, not a full screenshot
        if bbox[0] == 0 and bbox[1] == 0 and bbox[2] == resolution[0] and bbox[3] == resolution[1]:
            self.valid = False
            logger.warning(f'{self.file} is not cropped')
            return

        mean = get_color(image=image, area=bbox)
        mean = tuple(round(c) for c in mean)

        self.bbox = bbox
        self.mean = mean
        self.valid = True

    def fixup(self):
        """
        Fixup image

        Returns:
            bool: If file changed
        """
        return image_fixup(self.file)


@dataclass
class AssetData:
    path: str
    module: str
    asset: str
    lang: str
    frame: int

    images: List[AssetImage]

    area: Tuple[int, int, int, int] = ()
    search: Tuple[int, int, int, int] = ()
    color: Tuple[int, int, int] = ()
    button: Tuple[int, int, int, int] = ()

    has_raw_area = False
    has_raw_search = False
    has_raw_color = False
    has_raw_button = False

    @classmethod
    def product(cls, images: 'Slist[AssetImage]'):
        """
        Product DataAssets from AssetsImage with attr=""

        Args:
            images: images should have the same (module, asset, lang, frame)

        Returns:
            AssetData | None:
        """
        # sort files, files should start with base image
        base = None
        for image in images:
            if not image.attr:
                base = image
                break
        if base is None:
            logger.warning(f'No base assets in {images}')
            return None
        files = [image for image in images if image.attr]
        files.insert(0, base)

        # product data
        asset = cls(
            path=base.path,
            module=base.module,
            asset=base.asset,
            lang=base.lang,
            frame=base.frame,
            images=files
        )
        return asset

    def load_attr(self, image):
        """
        Load attributes from an asset image

        Args:
            image (AssetsImage):
        """
        if image.attr == '':
            self.area = image.bbox
            self.color = image.mean
            self.button = image.bbox
        elif image.attr == 'search':
            self.search = image.bbox
            self.has_raw_search = True
        elif image.attr == 'button':
            self.button = image.bbox
            self.has_raw_button = True
        elif image.attr == 'color':
            self.color = image.mean
            self.has_raw_color = True
        elif image.attr == 'area':
            self.area = image.bbox
            self.has_raw_area = True
        else:
            logger.warning(f'Trying to load an image with unknown attribute: {image}')

    def load(self):
        for image in self.images:
            image.load()
            if not image.valid:
                continue
            self.load_attr(image)

    def populate_search(self):
        """
        Populate asset.search
        """
        # Generate `search` from `area`
        if not self.has_raw_search and self.area:
            search = Area(self.area).pad(Const.ASSETS_SEARCH_PAD).limit_in_screen(Const.ASSETS_RESOLUTION)
            self.search = search


class AssetMultilang:
    def __init__(self, files):
        """
        Args:
            files (Slist[AssetImage]): list of files.
                files should have the same (module, asset)
        """
        first = files.first_or_none()
        if first is not None:
            self.module = first.module
            self.asset = first.asset
        else:
            self.module = '__unknown__'
            self.asset = '__unknown__'

        files.index_create('lang', 'frame')
        assets = defaultdict(dict)
        for data in files.index.values():
            asset = AssetData.product(data)
            if asset is not None:
                assets[asset.lang][asset.frame] = asset
        # sort lang then frame
        new = {}
        for lang in Const.ASSETS_LANG:
            frames = assets.get(lang, None)
            if frames:
                frames = dict(sorted(frames.items()))
                new[lang] = frames
            else:
                new[lang] = {}

        # <lang>:
        #   <frame>:
        #       AssetData
        self.dict_lang_frame: "dict[str, dict[int, AssetData]]" = new

    def __iter__(self) -> "Iterator[AssetData]":
        for lang, dict_frame in self.dict_lang_frame.items():
            for asset in dict_frame.values():
                yield asset

    @cached_property
    def images(self):
        count = 0
        for _ in self:
            count += 1
        return count

    def __str__(self):
        return f'{self.__class__.__name__}(module="{self.module}", name="{self.asset}", images={self.images})'

    def load(self):
        """
        Load images of lang/frame
        """
        for image in self:
            image.load()

    @cached_property
    def has_share(self):
        try:
            if self.dict_lang_frame['share']:
                return True
            else:
                return False
        except KeyError:
            return False

    def populate_attr_from_first_frame(self):
        """
        If an attribute is set in the first frame, apply to all
        """
        for lang, dict_frame in self.dict_lang_frame.items():
            if len(dict_frame) <= 1:
                continue
            # get first frame
            first_index = -1
            first_frame = None
            for index, frame in dict_frame.items():
                first_index = index
                first_frame = frame
                break
            if first_frame is None:
                continue

            # iter rest of the frames
            for index, frame in dict_frame.items():
                if index == first_index:
                    continue
                # copy attributes
                if not frame.has_raw_area and first_frame.has_raw_area:
                    frame.area = first_frame.area
                if not frame.has_raw_search and first_frame.has_raw_search:
                    frame.search = first_frame.search
                if not frame.has_raw_color and first_frame.has_raw_color:
                    frame.color = first_frame.color
                if not frame.has_raw_button and first_frame.has_raw_button:
                    frame.button = first_frame.button


class AssetModule:
    def __init__(self, files):
        """
        Args:
            files (Slist[AssetImage]): list of files.
                files should have the same (module, )
        """
        first = files.first_or_none()
        if first is not None:
            self.module = first.module
        else:
            self.module = '__unknown__'

        files.index_create('asset')
        assets = {}
        for name, data in files.index.items():
            name = name[0]
            data = AssetMultilang(data)
            assets[name] = data
        # sort by name
        assets = dict(sorted(assets.items()))

        self.dict_assets: "dict[str, AssetMultilang]" = assets

    def __iter__(self) -> "Iterator[AssetMultilang]":
        for asset in self.dict_assets.values():
            yield asset

    def __str__(self):
        return f'{self.__class__.__name__}(module="{self.module}", assets={len(self.dict_assets)})'

    def load(self):
        """
        Load all assets in module
        """
        with WORKER_POOL.wait_jobs() as pool:
            for image in self:
                pool.start_thread_soon(image.load())


class AssetAll:
    def __init__(self, files):
        """
        Convert all files to

        AssetAll:
            AssetModule:
                AssetMultilang:
                    AssetData:

        Args:
            files (Slist[str] | callable):
                list of files, or a function that iters files
        """
        if callable(files):
            files = Slist(list(files()))
        elif isinstance(files, Slist):
            pass
        elif isinstance(files, list):
            files = Slist(files)
        else:
            raise ValueError(f'Unexpected files given to AssetAll: {files}')

        self.total = files.count

        files.index_create('module')
        assets = {}
        for name, data in files.index.items():
            name = name[0]
            data = AssetModule(data)
            assets[name] = data
        self.dict_module: "dict[str, AssetModule]" = assets

    def __iter__(self) -> "Iterator[AssetModule]":
        for asset in self.dict_module.values():
            yield asset

    def __str__(self):
        return f'{self.__class__.__name__}(modules={len(self.dict_module)}, total={self.total})'

    def load(self, modules=None):
        """
        Load all assets

        Args:
            modules (set[str]): To load given modules only
        """
        with WORKER_POOL.wait_jobs() as pool:
            for module in self:
                if modules is not None:
                    if module.module not in modules:
                        continue
                for image in module:
                    pool.start_thread_soon(image.load)
