import base64
from typing import Dict, List, Literal

from msgspec import Struct, field

from alasio.assets.model.generator import AssetGenerator
from alasio.assets.model.name import to_asset_name, to_resource_name, validate_asset_name, validate_resource_name
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
from alasio.ext.path.calc import get_name, get_rootstem, get_suffix, uppath
from alasio.ext.path.iter import iter_entry
from alasio.ext.path.validate import validate_filename
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

        local_resource_files = set()
        local_template_files = set()

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
                local_template_files.add(name)
                continue
            # add resource
            if name.startswith('~'):
                local_resource_files.add(name)
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
                local_resource_files.add(new)
                resources[name] = ResourceRow(name=new, status='not_tracked')
                continue

        # add resource that doesn't exist on local
        for name in resource_data:
            n = removeprefix(name, '~')
            if n not in resources:
                resources[n] = ResourceRow(name=name, status='not_downloaded')

        # update MetaTemplate.source_exist
        for asset in self.assets.values():
            for template in asset.templates:
                if template.source:
                    template.source_exist = template.source in local_resource_files
                else:
                    template.source_exist = False
                template.file_exist = get_name(template.file) in local_template_files

        # sort
        folders = sorted(folders)
        resources = dict(sorted(resources.items()))
        # return
        return FolderResponse(
            mod_name=self.entry.name, path=self.path, mod_path_assets=self.entry.path_assets,
            folders=folders, resources=resources, assets=self.assets
        )

    def getdata(self):
        return self.data

    def resource_add_base64(self, source, data):
        """
        Add resource file from base64 data

        Args:
            source (str): Resource file, with file extension, e.g. ~BATTLE_PREPARATION.webp
                Accept filename with and without "~", as "~" prefix will be added
            data (str): png/jpg/webp/gif file data in base64

        Raises:
            ValueError: If source invalid or data invalid
        """
        validate_filename(source)
        source = to_resource_name(source)

        try:
            # Decode the base64 string to bytes
            content = base64.b64decode(data)
        except (ValueError, TypeError) as e:
            raise ValueError(f'Invalid base64 data for {source}, {e}')

        # add resource does not mean track it
        name = self.resource_add_bytes(source, content, track=False)
        cached_property.pop(self, 'data')
        return name

    def resource_del_force(self, source):
        """
        Delete a resource without tracking its usage

        Args:
            source (str | list[str]): Resource file(s), with "~" and file extension, e.g. ~BATTLE_PREPARATION.webp
        """
        sources = source if isinstance(source, list) else [source]

        for src in sources:
            validate_resource_name(src)

            source_file = self.folder / src
            logger.info(f'Resource del force: {source_file}')

            # resource_untrack_force will write resources and cleanup cache
            self.resource_untrack_force(src)
            source_file.atomic_remove()

        cached_property.pop(self, 'data')

    def resource_track(self, source):
        """
        Args:
            source (str | list[str]): Source filename(s) with "~" and file extension, e.g. "~BATTLE_PREPARATION.png"

        Returns:
            str | list[str]: Resource name(s) added, e.g. ~BATTLE_PREPARATION.webp
        """
        sources = source if isinstance(source, list) else [source]
        is_list = isinstance(source, list)
        result = []

        for src in sources:
            validate_resource_name(src)

            source_file = self.folder / src
            logger.info(f'Resource track: {source_file}')
            try:
                content = atomic_read_bytes(source_file)
            except FileNotFoundError as e:
                raise ValueError(f'No such resource {src}, {e}')
            name = self.resource_add_bytes(src, content)
            if src != name:
                source_file.atomic_remove()
            result.append(name)

        cached_property.pop(self, 'data')
        return result if is_list else result[0]

    def resource_untrack_force(self, source):
        """
        Args:
            source (str | list[str]): Source filename(s) with "~" and file extension, e.g. "~BATTLE_PREPARATION.png"
        """
        sources = source if isinstance(source, list) else [source]

        for src in sources:
            validate_resource_name(src)
            row = self.resources.pop(src, None)
            if row is None:
                continue
            logger.info(f'Resource track force: {self.folder / row.name}')

        self.resources_write()
        cached_property.pop(self, 'data')

    def resource_to_asset(self, source, override=False):
        """
        Args:
            source (str | list[str]): Source filename(s) with "~" and file extension, e.g. "~BATTLE_PREPARATION.png"
            override (bool): True to override existing asset, false to create with random suffix
        """
        sources = source if isinstance(source, list) else [source]

        # resource_track already validates source
        for src in sources:
            src = self.resource_track(src)
            source_file = self.folder / src
            logger.info(f'Resource to asset: {source_file}')
            try:
                image = image_load(source_file)
            except FileNotFoundError as e:
                raise ValueError(f'No such resource {src}, {e}')
            except ImageBroken as e:
                raise ValueError(f'Resource file broken {src}, {e}')
            area = get_bbox(image)
            color = RGB(get_color(image, area)).as_uint8()

            # get name
            name = to_asset_name(get_rootstem(src))
            assets = self.assets
            if not override and name in assets:
                name = f'{name}_{random_id()}'
            # create info
            template = MetaTemplate(area=area, color=color, source=src)
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
        cached_property.pop(self, 'data')
        return True

    def asset_add(self, asset_name):
        """
        Create an empty asset without templates
        """
        validate_asset_name(asset_name)
        asset_name = to_asset_name(asset_name)
        if asset_name in self.assets:
            raise ValueError(f'Asset name already exists: {asset_name}')

        logger.info(f'Asset add: {asset_name}')
        asset = MetaAsset(path=self.path, name=asset_name, templates=())
        self._validate_asset(asset)

        self.assets[asset.name] = asset
        self.asset_codegen()
        cached_property.pop(self, 'data')

    def asset_del(self, asset_name):
        """
        Delete an asset and all its templates

        Args:
            asset_name (str | list[str]): Name(s) of the asset(s) to delete

        Returns:
            bool: True if asset(s) were deleted
        """
        asset_names = asset_name if isinstance(asset_name, list) else [asset_name]
        assets = self.assets
        deleted_any = False

        for name in asset_names:
            validate_asset_name(name)
            asset = assets.pop(name, None)
            if not asset:
                continue

            logger.info(f'Asset del: {name}')
            # Remove all template files
            for t in asset.templates:
                self._template_remove(t)
            deleted_any = True

        if deleted_any:
            self.asset_codegen()
            cached_property.pop(self, 'data')
        return deleted_any

    def resource_rename(self, old, new):
        """
        Args:
            old (str): Old filename, with file extension, e.g. ~BATTLE_PREPARATION.webp
                Accept filename with and without "~", as "~" prefix will be added
            new (str): New filename, with file extension, e.g. ~BATTLE_PREPARATION.webp
                Accept filename with and without "~", as "~" prefix will be added
        """
        validate_filename(old)
        old = to_resource_name(old)
        validate_filename(new)
        new = to_resource_name(new)
        if old == new:
            raise ValueError(f'Resource name not changed: {old}')

        if new in self.resources:
            raise ValueError(f'Target resource already exists: {new}')

        # rename local file
        old_file = self.folder / old
        new_file = self.folder / new
        logger.info(f'Resource rename: {old_file} -> {new_file}')
        try:
            old_file.atomic_rename(new_file)
        except FileNotFoundError:
            # ignore FileNotFoundError as resource might not be downloaded yet
            pass
        except FileExistsError as e:
            raise ValueError(e)

        resource = self.resources.get(old, None)
        if resource is not None:
            if self.gitadd:
                self.gitadd.stage_add(new_file)
            resource.name = new
            # re-generate self.resources with resource.name
            data = {v.name: v for v in self.resources.values()}
            cached_property.set(self, 'resources', data)
            self.resources_write()
        else:
            # rename untracked resource
            pass

        # rename usages in asset
        modified = True
        for asset in self.assets.values():
            if old in asset.ref:
                asset.ref = tuple(new if ref == old else ref for ref in asset.ref)
                modified = True
            for template in asset.templates:
                if template.source == old:
                    template.source = new
                    modified = True
        if modified:
            self.asset_codegen()
        cached_property.pop(self, 'data')

    def asset_rename(self, old, new):
        """
        Args:
            old (str):
            new (str):
        """
        validate_asset_name(old)
        validate_asset_name(new)
        new = to_asset_name(new)
        if old == new:
            raise ValueError(f'Asset name not changed: {old}')

        asset = self.assets.get(old, None)
        if asset is None:
            raise ValueError(f'No such asset {old}')
        if new in self.assets:
            raise ValueError(f'Target asset already exists: {new}')

        logger.info(f'Asset rename: {old} -> {new}')
        asset.name = new
        for template in asset.templates:
            try:
                self._template_rename(template, name=new)
            except FileExistsError as e:
                raise ValueError(e)

        # re-generate self.resources with resource.name
        data = {v.name: v for v in self.assets.values()}
        cached_property.set(self, 'assets', data)
        self.asset_codegen()
        cached_property.pop(self, 'data')


if __name__ == '__main__':
    _entry = DICT_MOD_ENTRY['example_mod'].copy()
    _entry.root = env.PROJECT_ROOT.joinpath('ExampleMod')
    self = AssetFolder(_entry, 'assets/combat')
    print(self.data.assets)
