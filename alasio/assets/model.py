from typing import Dict, Literal, Optional, Tuple, Union

from msgspec import Struct, field
from msgspec.json import Decoder

from alasio.ext.cache import cached_property
from alasio.ext.file.msgspecfile import JsonCacheTTL
from alasio.ext.singleton import Singleton


class ResourceData(Struct, omit_defaults=True):
    # filename, resource filename always startswith "~"
    # e.g. ~BATTLE_PREPARATION.png, ~Screenshot_xxx.png
    name: str
    # file sha1
    sha1: str
    # file size
    size: int
    # image shape, (width, height, channel)
    shape: Tuple[int, int, int]


class TemplateData(Struct, omit_defaults=True, dict=True):
    # filename, e.g. BATTLE_PREPARATION.png, PAUSE.cn.2.png
    name: str
    # path to resource file, e.g. assets/combat/Screenshot_xxx.png
    resource: str

    # Area to crop from resource image
    # (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
    area: Tuple[int, int, int, int]
    # Average color of cropped image
    # (r, g, b) or value for monochrome image
    color: Union[Tuple[int, int, int], int]
    # Area to search from screenshot, `search` is 20px outer pad of `area` by default
    # values in `search` can be negative meaning right aligned, this is useful for dynamic resolution ratio
    search: Tuple[int, int, int, int]
    # Area to click if template is match exactly on `area`
    # If matched result moves, `button` moves accordingly.
    # This is useful when you do appear_then_click(...) on an movable content, click area is auto moved.
    button: Tuple[int, int, int, int]
    # Mark assets as server specific, empty string '' for shared assets
    # On CN server, assets with server=='cn' and server=='' will be loaded, assets from other server won't.
    server: str = ''
    # Frame index.
    # Alasio will try matching all frames, if any of them matched, considered as matched.
    # This is useful to do appear(...) on multiple images to handle dynamic contents or contents with minor difference
    frame: int = 1
    # Detection method
    # - template for template matching
    # - color for average color matching
    # - luma for template matching under luma channel
    # - custom for always false, you should override with your own detect function
    det: Literal[
        'template', 'color', 'template_color',
        'template_luma', 'template_luma_color',
        'custom',
    ] = 'template_luma_color'
    # Template matching similarity
    # result similarity > 0.85 is considered matched
    similarity: float = 0.85
    # Average color matching threshold
    # Average color difference < 30 considered matched
    threshold: Tuple[int, float] = 30


class AssetData(Struct, omit_defaults=True):
    # button name, e.g. BATTLE_PREPARATION
    name: str

    # Button level default attributes, see ButtonImageData
    # Attribute priority: template-level > button-level > default
    search: Optional[Tuple[int, int, int, int]] = None
    button: Optional[Tuple[int, int, int, int]] = None
    det: Optional[Literal[
        'template', 'color', 'template_color',
        'template_luma', 'template_luma_color',
        'custom',
    ]] = None
    similarity: Optional[float] = None
    threshold: Optional[Tuple[int, float]] = None

    # Ban current button after clicking (directly return no match within 5 seconds and 10 screenshots)
    # This is useful to avoid double-clicking
    ban: Union[int, float] = 5
    # List of template images
    templates: Dict[str, TemplateData] = field(default_factory=dict)


class DecoderCache(metaclass=Singleton):
    @cached_property
    def MODEL_RESOURCE_DATA(self):
        return Decoder(Dict[str, ResourceData])

    @cached_property
    def MODEL_ASSET_DATA(self):
        return Decoder(Dict[str, AssetData])


DECODER_CACHE = DecoderCache()
ASSET_JSON_CACHE = JsonCacheTTL()
