from typing import Dict, List, Literal

from msgspec import Struct, field

from alasio.assets.model.generator import AssetGenerator
from alasio.assets.model.name import to_asset_name, validate_resource_name
from alasio.assets.model.parser import MetaAsset, MetaTemplate
from alasio.assets.model.resource import ResourceManager
from alasio.base.image.color import get_color
from alasio.base.image.draw import get_bbox
from alasio.base.image.imfile import ImageBroken, crop, image_load, image_size
from alasio.base.op import Area, RGB, random_id
from alasio.config.entry.const import DICT_MOD_ENTRY
from alasio.ext import env
from alasio.ext.backport import removeprefix
from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_read_bytes, atomic_replace
from alasio.ext.path.calc import get_name, get_rootstem, get_suffix
from alasio.ext.path.iter import iter_entry
from alasio.logger import logger


class ResourceRow(Struct):
    # filename, resource filename always startswith "~"
    # e.g. ~BATTLE_PREPARATION.png, ~Screenshot_xxx.png
    name: str
    # whether local file exist
    # An resource file can be one of these type:
    # - tracked in resource.json and local file exists
    # - tracked in resource.json but not downloaded yet
    # - a local file not tracked in resource.json
    status: Literal['tracked', 'not_tracked', 'not_downloaded']


class FolderResponse(Struct):
    mod_name: str
    # Relative path from mod root to assets folder. e.g. assets
    # `path` must startswith `mod_path_assets`
    mod_path_assets: str
    # Relative path from project root to assets folder. e.g. assets/combat
    path: str
    # Sub-folders, list of folder names
    folders: List[str] = field(default_factory=list)
    # All resources
    # key: resource name for display, without "~" prefix
    # value: ResourceRow
    resources: Dict[str, ResourceRow] = field(default_factory=dict)
    # All assets
    # key: button_name, e.g. BATTLE_PREPARATION
    # value: MetaAsset
    assets: Dict[str, MetaAsset] = field(default_factory=dict)


class AssetFolder(AssetGenerator, ResourceManager):

    @cached_property
    def data(self):
        folders = []
        resources = {}
        # valid file extension of assets
        # png is commonly used because jpg is a lossy compression
        asset_ext = {'.png', '.jpg', '.gif', '.webp'}
        resource_data = self.resources
        known_asset_file = set()
        for asset in self.assets.values():
            for template in asset.templates:
                known_asset_file.add(get_name(template.file))

        # iter local folder
        for entry in iter_entry(self.folder, follow_symlinks=False):
            # add folder
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except FileNotFoundError:
                continue
            name = entry.name
            if is_dir:
                folders.append(name)
                continue
            # check suffix
            if get_suffix(name) not in asset_ext:
                continue
            # known asset
            if name in known_asset_file:
                continue
            # add resource
            if name.startswith('~'):
                if name in resource_data:
                    # known resource
                    n = removeprefix(name, '~')
                    resources[n] = ResourceRow(name=name, status='tracked')
                else:
                    # untracked resource
                    n = removeprefix(name, '~')
                    resources[n] = ResourceRow(name=name, status='not_tracked')
                continue
            else:
                # convert any unknown image as untracked resource
                old_file = self.folder.joinpath(name)
                new = f'~{name}'
                new_file = self.folder.joinpath(new)
                logger.info(f'Rename resource: {old_file}')
                try:
                    atomic_replace(old_file, new_file)
                except Exception as e:
                    logger.error(e)
                    continue
                resources[name] = ResourceRow(name=new, status='not_tracked')
                continue

        # add resource that doesn't exist on local
        for name in resource_data:
            n = removeprefix(name, '~')
            if n not in resources:
                resources[n] = ResourceRow(name=name, status='not_downloaded')

        # sort
        folders = sorted(folders)
        resources = dict(sorted(resources.items()))
        # return
        return FolderResponse(
            mod_name=self.entry.name, path=self.path, mod_path_assets=self.entry.path_assets,
            folders=folders, resources=resources, assets=self.assets
        )

    def resource_add_base64(self, file, data):
        """
        Add resource file from base64 data

        Args:
            file (str): Resource file, with "~" and file extension, e.g. ~BATTLE_PREPARATION.webp
            data (str): png/jpg/webp/gif file data in base64
        """
        pass

    def resource_del_force(self, file):
        """
        Delete a resource without tracking its usage

        Args:
            file (str): Resource file, with "~" and file extension, e.g. ~BATTLE_PREPARATION.webp
        """
        pass

    def resource_track(self, source):
        """
        Args:
            source (str): Source filename, e.g. "~BATTLE_PREPARATION.png"

        Returns:
            str: Resource name added, e.g. ~BATTLE_PREPARATION.webp
        """
        validate_resource_name(source)

        source_file = self.folder / source
        try:
            content = atomic_read_bytes(source_file)
        except FileNotFoundError as e:
            raise ValueError(f'No such resource {source}, {e}')
        name = self.resource_add_bytes(source, content)
        if source != name:
            source_file.atomic_remove()
        return name

    def resource_untrack_force(self, source):
        """
        Args:
            source (str): Source filename, e.g. "~BATTLE_PREPARATION.png"
        """
        validate_resource_name(source)
        row = self.resources.pop(source)
        if row is None:
            return
        self.resources_write()

    def resource_to_asset(self, source, override=False):
        """
        Args:
            source (str): Source filename, e.g. "~BATTLE_PREPARATION.png"
            override (bool): True to override existing asset, false to create with random suffix
        """
        source = self.resource_track(source)
        source_file = self.folder / source
        try:
            image = image_load(source_file)
        except FileNotFoundError as e:
            raise ValueError(f'No such resource {source}, {e}')
        except ImageBroken as e:
            raise ValueError(f'Resource file broken {source}, {e}')
        area = get_bbox(image)
        color = RGB(get_color(image, area)).as_uint8()

        # get name
        name = to_asset_name(get_rootstem(source))
        assets = self.assets
        if not override and name in assets:
            name = f'{name}_{random_id()}'
        # create info
        template = MetaTemplate(area=area, color=color, source=source)
        asset = MetaAsset(path=self.path, name=name, templates=(template,))
        self._validate_asset(asset)
        logger.info(f'New asset: {asset}')

        # create template file
        if Area.from_size(image_size(image)) == area:
            # not a mask image
            self._template_generate(template, image=image)
        else:
            # save cropped mask image
            im = crop(image, area, copy=False)
            self._template_generate(template, image=im)

        assets[asset.name] = asset
        self.asset_codegen()
        return True

    def asset_del(self, asset_name):
        """
        Delete an asset and all its templates

        Args:
            asset_name (str): Name of the asset to delete

        Returns:
            bool: True if asset was deleted
        """
        assets = self.assets
        if asset_name not in assets:
            return False

        logger.info(f'Deleted asset: {asset_name}')
        asset = assets.pop(asset_name, None)
        # Remove all template files
        if asset:
            for t in asset.templates:
                self._template_remove(t)

        self.asset_codegen()
        return True


if __name__ == '__main__':
    _entry = DICT_MOD_ENTRY['example_mod'].copy()
    _entry.root = env.PROJECT_ROOT.joinpath('ExampleMod')
    self = AssetFolder(_entry, 'assets/combat')
    print(self.data.assets)
