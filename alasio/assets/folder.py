from typing import Dict, List, Literal

from msgspec import Struct, field
from msgspec.structs import asdict

from alasio.assets.model import AssetData, DECODER_CACHE, ResourceData
from alasio.config.entry.const import ModEntryInfo
from alasio.ext.backport import removeprefix
from alasio.ext.cache import cached_property, set_cached_property
from alasio.ext.file.jsonfile import NoIndentNoSpace, write_json_custom_indent
from alasio.ext.msgspec_error import load_json_with_default
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, atomic_remove, atomic_replace, atomic_write
from alasio.ext.path.calc import get_name, get_suffix, to_posix
from alasio.ext.path.iter import iter_entry
from alasio.git.stage.hashobj import blob_hash
from alasio.logger import logger


def resource_from_file(source, target=None, fixup=False):
    """
    Args:
        source (str): Absolute filepath
        target (str): Optional target to write to
        fixup (bool): True to fixup image while reading (may write image)

    Returns:
        ResourceData:

    Raises:
        FileNotFoundError:
        ImageBroken:
    """
    import numpy as np
    from alasio.base.image.imfile import ImageBroken, image_decode, image_encode, image_shape
    name = get_name(source)
    # may raise FileNotFoundError
    content = atomic_read_bytes(source)
    data = np.frombuffer(content, dtype=np.uint8)
    if not data.size:
        raise ImageBroken('Empty image data')
    # may raise ImageBroken
    image = image_decode(data)
    shape = image_shape(image)

    if fixup:
        # see to image_fixup()
        _, _, ext = name.rpartition('.')
        # may raise ImageBroken
        data = image_encode(image, ext=ext)
        new_content = data.tobytes()
        if content != new_content:
            if target:
                atomic_write(target, new_content)
            else:
                atomic_write(source, new_content)
            content = new_content
        elif target:
            atomic_write(source, new_content)
    else:
        if target:
            atomic_write(target, content)

    # create object
    sha = blob_hash(content)
    return ResourceData(name=name, sha1=sha, size=len(content), shape=shape)


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
    folders: List[str] = field(default_factory=dict)
    # All resources
    # key: resource name for display, without "~" prefix
    # value: ResourceRow
    resources: Dict[str, ResourceRow] = field(default_factory=dict)
    # All assets
    # key: button_name, e.g. BATTLE_PREPARATION
    # value: AssetData
    assets: Dict[str, AssetData] = field(default_factory=dict)


class AssetFolder:
    def __init__(self, entry: ModEntryInfo, path):
        """
        Args:
            entry:
            path (str): Relative path from project root to assets folder. e.g. assets/combat
        """
        self.entry = entry
        self.root = PathStr.new(entry.root)
        self.path = to_posix(path)
        self.folder = self.root.joinpath(path)

    def __str__(self):
        return f'AssetFolder(root="{self.root.to_posix()}", path="{self.path}")'

    __repr__ = __str__

    def __hash__(self):
        return hash((self.folder, self.entry.name))

    @cached_property
    def resource_data(self) -> "dict[str, ResourceData]":
        """
        Load resource.json into cache
        key: filename, resource filename always startswith "~"
            e.g. ~BATTLE_PREPARATION.png, ~Screenshot_xxx.png
        value: ResourceData
        """
        file = self.folder.joinpath('resource.json')
        try:
            content = atomic_read_bytes(file)
        except FileNotFoundError:
            return {}
        decoder = DECODER_CACHE.MODEL_RESOURCE_DATA
        result, errors = load_json_with_default(content, model=Dict[str, ResourceData], decoder=decoder)
        for e in errors:
            logger.error(f'Error while reading {file}: {e}')
        # remove empty file
        if not result:
            atomic_remove(file)
            return {}
        return result

    def resource_data_write(self, data: "dict[str, ResourceData] | None" = None):
        """
        Write cached resource data into resource.json

        Args:
            data: new data to write, or None to write current data
        """
        if data is not None and data != self.resource_data:
            set_cached_property(self, 'resource_data', data)

        file = self.folder.joinpath('resource.json')
        logger.info(f'Write resource data: {file}')
        out = {}
        for name, resource in sorted(self.resource_data.items()):
            out[name] = NoIndentNoSpace(asdict(resource))
        if out:
            write_json_custom_indent(file, out)
        else:
            atomic_remove(file)

    @cached_property
    def asset_data(self) -> "dict[str, AssetData]":
        """
        Load asset.json into cache
        key: button name, e.g. BUTTON_PREPARATION
        value: AssetData
        """
        file = self.folder.joinpath('asset.json')
        try:
            content = atomic_read_bytes(file)
        except FileNotFoundError:
            return {}
        decoder = DECODER_CACHE.MODEL_ASSET_DATA
        result, errors = load_json_with_default(content, model=Dict[str, AssetData], decoder=decoder)
        for e in errors:
            logger.error(f'Error while reading {file}: {e}')
        # remove empty file
        if not result:
            atomic_remove(file)
            return {}
        return result

    def asset_data_write(self, data: "dict[str, ButtonData] | None" = None):
        """
        Write cached asset data into asset.json

        Args:
            data: new data to write, or None to write current data
        """
        if data is not None and data != self.asset_data:
            set_cached_property(self, 'asset_data', data)

        file = self.folder.joinpath('asset.json')
        logger.info(f'Write asset data: {file}')
        out = {}
        for name, asset in sorted(self.asset_data.items()):
            if asset.templates:
                asset.templates = dict(sorted(asset.templates.items()))
            out[name] = asdict(asset)
        if out:
            write_json_custom_indent(file, out)
        else:
            atomic_remove(file)

    @cached_property
    def data(self):
        folders = []
        resources = {}
        asset_ext = self.entry.asset_ext
        resource_data = self.resource_data
        known_asset_file = set()
        for asset in self.asset_data.values():
            for template in asset.templates.values():
                known_asset_file.add(template.name)

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
            mod_name=self.entry.name, path=self.path,
            folders=folders, resources=resources, assets=self.asset_data
        )

    def resource_add(self, source):
        """
        Add new resource to folder

        Args:
            source (str): Absolute filepath
        """
        name = get_name(source)
        target = self.folder.joinpath(name)

        resource = resource_from_file(source=source, target=target, fixup=True)
        _ = self.resource_data
        if self.resource_data.get(name, None) == resource:
            # same as existing
            pass
        else:
            # add or modify
            self.resource_data[name] = resource
            self.resource_data_write()

    def resource_del(self, file):
        """
        Args:
            file (str): Filename
        """
        _ = self.resource_data
        try:
            del self.resource_data[file]
        except KeyError:
            # no need to delete
            return
        else:
            self.resource_data_write()

        # also delete local file
        atomic_remove(file)
