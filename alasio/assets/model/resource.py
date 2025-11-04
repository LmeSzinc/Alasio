from typing import Tuple

import numpy as np
from msgspec import Struct, field
from msgspec.json import Decoder, Encoder

from alasio.assets.model.generator import AssetFolderBase
from alasio.base.image.imfile import ImageBroken, image_decode, image_encode, image_shape
from alasio.ext.cache import cached_property
from alasio.ext.path.atomic import atomic_read_bytes, atomic_remove, atomic_write
from alasio.ext.path.calc import get_stem
from alasio.ext.singleton import Singleton
from alasio.git.stage.hashobj import blob_hash
from alasio.logger import logger


class ResourceData(Struct):
    # filename, resource filename always startswith "~"
    # e.g. ~BATTLE_PREPARATION.png, ~Screenshot_xxx.png
    name: str = field(name='n')
    # file sha1
    sha1: str = field(name='h')
    # file size
    size: int = field(name='s')
    # image shape, (width, height, channel)
    shape: Tuple[int, int, int] = field(name='p')


class DecoderCache(metaclass=Singleton):
    @cached_property
    def MODEL_RESOURCE_DATA(self):
        return Decoder(ResourceData)

    @cached_property
    def ENCODER(self):
        return Encoder()


class ResourceManager(AssetFolderBase):
    @cached_property
    def resources(self) -> "dict[str, ResourceData]":
        """
        Load resource.json into cache
        key: filename, resource filename always startswith "~"
            e.g. ~BATTLE_PREPARATION.webp, ~Screenshot_xxx.webp
        value: ResourceData
        """
        file = self.resource_file
        try:
            content = atomic_read_bytes(file)
        except FileNotFoundError:
            return {}

        decoder = DecoderCache().MODEL_RESOURCE_DATA
        # no load_json_with_default because ResourceData has no default field, we can't fix any errors
        rows = decoder.decode_lines(content)
        data = {}
        for row in rows:
            # validate name
            name = row.name
            if not name.startswith('~') or not name.endswith('.webp'):
                logger.warning(f'Invalid resource name in json: {row}')
                continue
            data[row.name] = row

        if len(data) != len(rows):
            self.resources_write(data=data)
        return data

    def resources_write(self, data: "dict[str, ResourceData] | None" = None):
        """
        Write cached resource data into resource.json

        Args:
            data: new data to write, or None to write current data
        """
        file = self.resource_file
        logger.info(f'Write resource data: {file}')
        if data is None:
            data = self.resources

        rows = [v for _, v in sorted(data.items())]
        encoder = DecoderCache().ENCODER
        content = encoder.encode_lines(rows)

        # write
        if content:
            atomic_write(file, content)
        else:
            atomic_remove(file)

    def resource_add_bytes(self, filename: str, data: bytes, track=True):
        """
        Add resource from bytes data

        Args:
            filename: e.g. ~BATTLE_PREPARATION.png
            data: png/jpg/webp/gif file in bytes
            track (bool): True to track resource

        Returns:
            str: Resource name added, e.g. ~BATTLE_PREPARATION.webp

        Raises:
            ImageBroken:
        """
        name = get_stem(filename).lstrip('~')
        name = f'~{name}.webp'

        # decode
        data = np.frombuffer(data, dtype=np.uint8)
        if not data.size:
            raise ImageBroken('Empty image data')
        image = image_decode(data)
        shape = image_shape(image)

        # encode
        data = image_encode(image, ext='webp').tobytes()
        sha = blob_hash(data)
        resource = ResourceData(name=name, sha1=sha, size=len(data), shape=shape)

        # write
        file = self.folder / name
        logger.info(f'Resource add {file}')
        atomic_write(file, data)
        if track:
            self.resources[name] = resource
            self.resources_write()
        return name

    def resource_add_file(self, file):
        """
        Add resource from local file

        Args:
            file (str): Absolute filepath

        Raises:
            FileNotFoundError:
            ImageBroken:
        """
        data = atomic_read_bytes(file)
        self.resource_add_bytes(file, data)
