import pytest

from alasio.assets_dev.parse import AssetAll, AssetData, AssetModule, AssetMultilang
from alasio.base.op import Area, Slist
from alasio.config.const import Const
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.assets_dev.helpers import (
    build_data, build_images, build_module, build_multilang, parse_image, project_root, write_png
)


class TestAssetImageParse:
    """AssetImage parses module, asset, frame, language and attribute from the filename"""

    @pytest.mark.parametrize(
        "relative, module, asset, lang, frame, attr",
        [
            ('assets/share/a/X.png', 'a', 'X', 'share', 0, ''),
            ('assets/share/a/X.gif', 'a', 'X', 'share', 0, ''),
            ('assets/share/a/TEMPLATE_X.png', 'a', 'TEMPLATE_X', 'share', 0, ''),
            # frame
            ('assets/share/a/X.1.png', 'a', 'X', 'share', 1, ''),
            ('assets/share/a/X.12.png', 'a', 'X', 'share', 12, ''),
            # attribute
            ('assets/share/a/X.area.png', 'a', 'X', 'share', 0, 'area'),
            ('assets/share/a/X.search.png', 'a', 'X', 'share', 0, 'search'),
            ('assets/share/a/X.color.png', 'a', 'X', 'share', 0, 'color'),
            ('assets/share/a/X.button.png', 'a', 'X', 'share', 0, 'button'),
            # frame and attribute, in any order
            ('assets/share/a/X.2.search.png', 'a', 'X', 'share', 2, 'search'),
            ('assets/share/a/X.search.2.png', 'a', 'X', 'share', 2, 'search'),
            # language
            ('assets/share/a/X.en.3.png', 'a', 'X', 'en', 3, ''),
            ('assets/share/a/X.CN.png', 'a', 'X', 'cn', 0, ''),
            # the last attribute wins
            ('assets/share/a/X.area.color.png', 'a', 'X', 'share', 0, 'color'),
            # the parser ignores an unknown suffix, is_assets_file() rejects such a file
            ('assets/share/a/X.unknown.png', 'a', 'X', 'share', 0, ''),
            # nested module
            ('assets/share/combat/interact/X.png', 'combat/interact', 'X', 'share', 0, ''),
        ],
    )
    def test_parse(self, fs, relative, module, asset, lang, frame, attr):
        project = project_root(fs)
        image = parse_image(project, relative)
        assert image.file == project.joinpath(relative)
        assert image.path == relative
        assert image.module == module
        assert image.asset == asset
        assert image.lang == lang
        assert image.frame == frame
        assert image.attr == attr
        # not parsed yet
        assert image.valid is False
        assert image.bbox == ()
        assert image.mean == ()

    @pytest.mark.parametrize("lang", ['cn', 'en', 'jp', 'tw'])
    def test_language_folder_is_stripped(self, fs, lang):
        """The language folder is not part of the module, the extractor sets the language of it"""
        project = project_root(fs)
        image = parse_image(project, f'assets/{lang}/combat/X.png', lang=lang)
        assert image.path == f'assets/{lang}/combat/X.png'
        assert image.module == 'combat'
        # the language folder is only known by the extractor, not by the filename
        assert image.lang == 'share'

    def test_str(self, fs):
        project = project_root(fs)
        image = parse_image(project, 'assets/share/a/X.png')
        assert str(image) == f'AssetImage(file={project.joinpath("assets/share/a/X.png")}, valid=False)'


class TestAssetImageLoad:
    """AssetImage.load() reads bbox and mean color, and marks the image invalid"""

    def test_load_asset(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12), rgb=(10, 20, 30))
        image = parse_image(project, 'assets/share/a/X.png')
        with logger.mock_capture_writer() as capture:
            image.load()
        assert capture.concat() == ''
        assert image.valid is True
        assert image.bbox == (5, 4, 15, 12)
        assert image.mean == (10, 20, 30)

    def test_load_full_screenshot(self, fs, small_resolution):
        """Content filling the whole screen is a screenshot, not an asset"""
        project = project_root(fs)
        file = project.joinpath('assets/share/a/X.png')
        write_png(fs, file, area=(0, 0, 40, 20))
        image = parse_image(project, 'assets/share/a/X.png')
        with logger.mock_capture_writer() as capture:
            image.load()
        assert capture.concat() == f'WARNING | {file} is not cropped\n'
        assert image.valid is False
        assert image.bbox == ()
        assert image.mean == ()

    def test_load_broken_image(self, fs, small_resolution):
        project = project_root(fs)
        file = project.joinpath('assets/share/a/X.png')
        fs.create_file(file, contents=b'broken')
        image = parse_image(project, 'assets/share/a/X.png')
        with logger.mock_capture_writer() as capture:
            image.load()
        assert capture.concat() == f'WARNING | {file}: Empty image after cv2.imdecode\n'
        assert image.valid is False

    def test_load_missing_file(self, fs, small_resolution):
        project = project_root(fs)
        file = project.joinpath('assets/share/a/X.png')
        image = parse_image(project, 'assets/share/a/X.png')
        with logger.mock_capture_writer() as capture:
            image.load()
        assert capture.concat().startswith(f'WARNING | {file}: [Errno 2] ')
        assert image.valid is False

    def test_load_pure_black_image(self, fs, small_resolution):
        project = project_root(fs)
        file = project.joinpath('assets/share/a/X.png')
        # no content at all, there is no bbox in a pure black image
        write_png(fs, file, area=(0, 0, 0, 0))
        image = parse_image(project, 'assets/share/a/X.png')
        with logger.mock_capture_writer() as capture:
            image.load()
        assert capture.concat() == f'WARNING | {file}: Cannot get bbox from a pure black image\n'
        assert image.valid is False

    def test_load_template_skips_resolution_check(self, fs):
        """A template is a crop of the screen, it has no screen resolution"""
        project = project_root(fs)
        # Const.ASSETS_TEMPLATE_PREFIX is True by default, the resolution is 1280x720
        assert Const.ASSETS_TEMPLATE_PREFIX is True
        write_png(fs, project.joinpath('assets/share/a/TEMPLATE_X.png'), width=20, height=10, area=(1, 1, 5, 6))
        image = parse_image(project, 'assets/share/a/TEMPLATE_X.png')
        image.load()
        assert image.valid is True
        assert image.bbox == (1, 1, 5, 6)


class TestAssetDataProduct:
    """AssetData.product() takes the base image and the attribute images of a frame"""

    def test_base_image_first(self, fs):
        project = project_root(fs)
        # the base image is not necessarily the first file
        data = build_data(
            project,
            [
                ('share', 'assets/share/a/X.search.png'),
                ('share', 'assets/share/a/X.png'),
            ],
        )
        assert data.path == 'assets/share/a/X.png'
        assert data.module == 'a'
        assert data.asset == 'X'
        assert data.lang == 'share'
        assert data.frame == 0
        assert [image.path for image in data.images] == ['assets/share/a/X.png', 'assets/share/a/X.search.png']
        # not loaded yet
        assert data.area == ()
        assert data.search == ()
        assert data.color == ()
        assert data.button == ()
        assert data.has_raw_area is False
        assert data.has_raw_search is False
        assert data.has_raw_color is False
        assert data.has_raw_button is False

    def test_no_base_image(self, fs):
        project = project_root(fs)
        images = build_images(project, [('share', 'assets/share/a/X.search.png')])
        with logger.mock_capture_writer() as capture:
            data = AssetData.product(images)
        assert data is None
        assert capture.concat() == f'WARNING | No base assets in {images}\n'


class TestAssetDataLoad:
    """AssetData.load() fills area, search, color and button from the image files"""

    def test_load_base_image(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12), rgb=(10, 20, 30))
        data = build_data(project, [('share', 'assets/share/a/X.png')])
        data.load()
        # the base image gives area, color and button
        assert data.area == (5, 4, 15, 12)
        assert data.color == (10, 20, 30)
        assert data.button == (5, 4, 15, 12)
        # search is generated from area, not read from a file
        assert data.search == ()
        assert data.has_raw_area is False
        assert data.has_raw_search is False
        assert data.has_raw_color is False
        assert data.has_raw_button is False

    def test_load_attribute_images(self, fs, small_resolution):
        project = project_root(fs)
        folder = project.joinpath('assets/share/a')
        write_png(fs, folder.joinpath('X.png'), area=(5, 4, 15, 12), rgb=(10, 20, 30))
        write_png(fs, folder.joinpath('X.area.png'), area=(3, 1, 25, 16))
        write_png(fs, folder.joinpath('X.search.png'), area=(1, 2, 20, 18))
        write_png(fs, folder.joinpath('X.color.png'), area=(0, 0, 10, 10), rgb=(1, 2, 3))
        write_png(fs, folder.joinpath('X.button.png'), area=(2, 3, 30, 15))
        data = build_data(
            project,
            [
                ('share', 'assets/share/a/X.png'),
                ('share', 'assets/share/a/X.area.png'),
                ('share', 'assets/share/a/X.search.png'),
                ('share', 'assets/share/a/X.color.png'),
                ('share', 'assets/share/a/X.button.png'),
            ],
        )
        data.load()
        # every attribute image overrides the value of the base image
        assert data.area == (3, 1, 25, 16)
        assert data.search == (1, 2, 20, 18)
        assert data.color == (1, 2, 3)
        assert data.button == (2, 3, 30, 15)
        assert data.has_raw_area is True
        assert data.has_raw_search is True
        assert data.has_raw_color is True
        assert data.has_raw_button is True

    def test_load_skips_invalid_image(self, fs, small_resolution):
        project = project_root(fs)
        fs.create_file(project.joinpath('assets/share/a/X.png'), contents=b'broken')
        data = build_data(project, [('share', 'assets/share/a/X.png')])
        data.load()
        assert data.area == ()
        assert data.color == ()
        assert data.button == ()

    def test_load_unknown_attribute(self):
        """An image with an unknown attribute is ignored with a warning"""
        data = AssetData(path='', module='a', asset='X', lang='share', frame=0, images=[])

        class FakeImage:
            attr = 'gift'
            bbox = (5, 4, 15, 12)
            mean = (10, 20, 30)

            def __str__(self):
                return 'FakeImage()'

        with logger.mock_capture_writer() as capture:
            data.load_attr(FakeImage())
        assert capture.concat() == 'WARNING | Trying to load an image with unknown attribute: FakeImage()\n'
        assert data.area == ()
        assert data.search == ()
        assert data.color == ()
        assert data.button == ()


class TestAssetDataPopulateSearch:
    """AssetData.populate_search() generates search from area"""

    def test_search_from_area(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12))
        data = build_data(project, [('share', 'assets/share/a/X.png')])
        data.load()
        data.populate_search()
        # area outset by ASSETS_SEARCH_OUTSET, then limited in the screen
        assert isinstance(data.search, Area)
        assert data.search == (0, 0, 35, 20)

    def test_search_outset(self, fs):
        project = project_root(fs)
        Const.ASSETS_RESOLUTION = (100, 100)
        write_png(fs, project.joinpath('assets/share/a/X.png'), width=100, height=100, area=(40, 40, 60, 60))
        data = build_data(project, [('share', 'assets/share/a/X.png')])
        data.load()
        data.populate_search()
        assert data.search == (20, 20, 80, 80)

    def test_raw_search_is_kept(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12))
        write_png(fs, project.joinpath('assets/share/a/X.search.png'), area=(7, 6, 17, 14))
        data = build_data(
            project,
            [
                ('share', 'assets/share/a/X.png'),
                ('share', 'assets/share/a/X.search.png'),
            ],
        )
        data.load()
        data.populate_search()
        assert data.search == (7, 6, 17, 14)

    def test_no_area(self, fs, small_resolution):
        # nothing loaded, there is no area to generate a search from
        project = project_root(fs)
        data = build_data(project, [('share', 'assets/share/a/X.png')])
        data.populate_search()
        assert data.search == ()


class TestAssetMultilang:
    """AssetMultilang groups the frames of an asset by language"""

    def test_group_by_lang_and_frame(self, fs, src_languages):
        project = project_root(fs)
        asset = build_multilang(
            project,
            [
                ('share', 'assets/share/a/X.2.png'),
                ('share', 'assets/share/a/X.png'),
                ('cn', 'assets/cn/a/X.png'),
            ],
        )
        assert asset.module == 'a'
        assert asset.asset == 'X'
        # every language is present, even the ones without assets
        assert list(asset.dict_lang_frame) == ['share', 'cn', 'en']
        assert list(asset.dict_lang_frame['share']) == [0, 2]
        assert list(asset.dict_lang_frame['cn']) == [0]
        assert asset.dict_lang_frame['en'] == {}

    def test_frames_are_sorted(self, fs, src_languages):
        project = project_root(fs)
        asset = build_multilang(
            project,
            [
                ('share', 'assets/share/a/X.10.png'),
                ('share', 'assets/share/a/X.2.png'),
            ],
        )
        assert list(asset.dict_lang_frame['share']) == [2, 10]

    def test_iter(self, fs, src_languages):
        project = project_root(fs)
        asset = build_multilang(
            project,
            [
                ('share', 'assets/share/a/X.2.png'),
                ('share', 'assets/share/a/X.png'),
                ('cn', 'assets/cn/a/X.png'),
            ],
        )
        assert [(data.lang, data.frame) for data in asset] == [('share', 0), ('share', 2), ('cn', 0)]

    def test_unknown_asset(self):
        asset = AssetMultilang(Slist([]))
        assert asset.module == '__unknown__'
        assert asset.asset == '__unknown__'
        assert asset.dict_lang_frame == {'cn': {}, 'en': {}, 'jp': {}, 'tw': {}}
        assert asset.has_share is False
        assert asset.images == 0

    def test_has_share(self, fs, src_languages):
        project = project_root(fs)
        assert build_multilang(project, [('share', 'assets/share/a/X.png')]).has_share is True
        assert build_multilang(project, [('cn', 'assets/cn/a/X.png')]).has_share is False

    def test_has_share_without_share_language(self, fs):
        """Alas has no share language, has_share must not raise"""
        project = project_root(fs)
        assert build_multilang(project, [('cn', 'assets/cn/a/X.png')]).has_share is False

    def test_images_count(self, fs, src_languages):
        project = project_root(fs)
        asset = build_multilang(
            project,
            [
                ('share', 'assets/share/a/X.png'),
                ('share', 'assets/share/a/X.2.png'),
                ('cn', 'assets/cn/a/X.png'),
            ],
        )
        # one image per frame
        assert asset.images == 3

    def test_str(self, fs, src_languages):
        project = project_root(fs)
        asset = build_multilang(project, [('share', 'assets/share/a/X.png')])
        assert str(asset) == 'AssetMultilang(module="a", name="X", images=1)'

    def test_load(self, fs, src_languages, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12))
        write_png(fs, project.joinpath('assets/share/a/X.2.png'), area=(1, 1, 9, 9))
        asset = build_multilang(
            project,
            [
                ('share', 'assets/share/a/X.png'),
                ('share', 'assets/share/a/X.2.png'),
            ],
        )
        asset.load()
        assert [frame.area for frame in asset] == [(5, 4, 15, 12), (1, 1, 9, 9)]


class TestAssetMultilangPopulateAttr:
    """populate_attr_from_first_frame() copies the attributes set in the first frame"""

    def make_asset(self, project, extra):
        return build_multilang(
            project,
            [
                ('share', 'assets/share/a/X.png'),
                ('share', 'assets/share/a/X.area.png'),
                ('share', 'assets/share/a/X.search.png'),
                ('share', 'assets/share/a/X.color.png'),
                ('share', 'assets/share/a/X.button.png'),
                ('share', 'assets/share/a/X.2.png'),
                *extra,
            ],
        )

    def write_files(self, fs, project, extra):
        folder = project.joinpath('assets/share/a')
        write_png(fs, folder.joinpath('X.png'), area=(5, 4, 15, 12), rgb=(10, 20, 30))
        write_png(fs, folder.joinpath('X.area.png'), area=(3, 1, 25, 16))
        write_png(fs, folder.joinpath('X.search.png'), area=(1, 2, 20, 18))
        write_png(fs, folder.joinpath('X.color.png'), area=(0, 0, 10, 10), rgb=(1, 2, 3))
        write_png(fs, folder.joinpath('X.button.png'), area=(2, 3, 30, 15))
        write_png(fs, folder.joinpath('X.2.png'), area=(6, 5, 14, 11), rgb=(40, 50, 60))
        for name, kwargs in extra:
            write_png(fs, folder.joinpath(name), **kwargs)

    def test_copy_from_first_frame(self, fs, src_languages, small_resolution):
        project = project_root(fs)
        self.write_files(fs, project, extra=[])
        asset = self.make_asset(project, extra=[])
        asset.load()
        asset.populate_attr_from_first_frame()
        first, second = asset.dict_lang_frame['share'].values()
        assert second.area == first.area == (3, 1, 25, 16)
        assert second.search == first.search == (1, 2, 20, 18)
        assert second.color == first.color == (1, 2, 3)
        assert second.button == first.button == (2, 3, 30, 15)
        # the second frame has no image of its own for these attributes
        assert second.has_raw_area is False
        assert second.has_raw_search is False
        assert second.has_raw_color is False
        assert second.has_raw_button is False

    def test_raw_attribute_of_later_frame_is_kept(self, fs, src_languages, small_resolution):
        project = project_root(fs)
        extra = [('X.2.area.png', dict(area=(7, 6, 13, 10)))]
        self.write_files(fs, project, extra=extra)
        asset = self.make_asset(project, extra=[('share', 'assets/share/a/X.2.area.png')])
        asset.load()
        asset.populate_attr_from_first_frame()
        first, second = asset.dict_lang_frame['share'].values()
        assert first.area == (3, 1, 25, 16)
        assert second.area == (7, 6, 13, 10)
        assert second.has_raw_area is True

    def test_nothing_to_copy(self, fs, src_languages, small_resolution):
        """The first frame has no raw attribute, the other frames keep their own values"""
        project = project_root(fs)
        folder = project.joinpath('assets/share/a')
        write_png(fs, folder.joinpath('X.png'), area=(5, 4, 15, 12))
        write_png(fs, folder.joinpath('X.2.png'), area=(6, 5, 14, 11))
        asset = build_multilang(
            project,
            [
                ('share', 'assets/share/a/X.png'),
                ('share', 'assets/share/a/X.2.png'),
            ],
        )
        asset.load()
        asset.populate_attr_from_first_frame()
        first, second = asset.dict_lang_frame['share'].values()
        assert first.area == (5, 4, 15, 12)
        assert second.area == (6, 5, 14, 11)


class TestAssetModule:
    """AssetModule groups the assets of a module by name"""

    def test_group_assets(self, fs, src_languages):
        project = project_root(fs)
        module = build_module(
            project,
            [
                ('share', 'assets/share/a/Y.png'),
                ('share', 'assets/share/a/X.2.png'),
                ('share', 'assets/share/a/X.png'),
            ],
        )
        assert module.module == 'a'
        # sorted by asset name
        assert list(module.dict_assets) == ['X', 'Y']
        assert [str(asset) for asset in module] == [
            'AssetMultilang(module="a", name="X", images=2)',
            'AssetMultilang(module="a", name="Y", images=1)',
        ]

    def test_unknown_module(self):
        module = AssetModule(Slist([]))
        assert module.module == '__unknown__'
        assert module.dict_assets == {}
        assert list(module) == []

    def test_str(self, fs, src_languages):
        project = project_root(fs)
        module = build_module(
            project,
            [
                ('share', 'assets/share/a/X.png'),
                ('share', 'assets/share/a/Y.png'),
            ],
        )
        assert str(module) == 'AssetModule(module="a", assets=2)'

    def test_load(self, fs, src_languages, small_resolution):
        """load() loads every asset of the module, on the thread pool"""
        project = project_root(fs)
        folder = project.joinpath('assets/share/a')
        write_png(fs, folder.joinpath('X.png'), area=(5, 4, 15, 12))
        write_png(fs, folder.joinpath('Y.3.png'), area=(1, 1, 9, 9))
        module = build_module(
            project,
            [
                ('share', 'assets/share/a/X.png'),
                ('share', 'assets/share/a/Y.3.png'),
            ],
        )
        module.load()
        assert [(asset.asset, frame.area) for asset in module for frame in asset] == [
            ('X', (5, 4, 15, 12)),
            ('Y', (1, 1, 9, 9)),
        ]


class TestAssetAll:
    """AssetAll groups the images of all modules"""

    def test_group_modules(self, fs, src_languages):
        project = project_root(fs)
        images = build_images(
            project,
            [
                ('share', 'assets/share/b/Y.png'),
                ('share', 'assets/share/a/X.png'),
            ],
        )
        assets = AssetAll(images)
        assert assets.total == 2
        assert list(assets.dict_module) == ['b', 'a']
        assert [module.module for module in assets] == ['b', 'a']
        assert str(assets) == 'AssetAll(modules=2, total=2)'

    def test_from_callable(self, fs, src_languages):
        project = project_root(fs)
        images = build_images(project, [('share', 'assets/share/a/X.png')])
        assets = AssetAll(lambda: iter(images))
        assert assets.total == 1
        assert list(assets.dict_module) == ['a']

    def test_from_list(self, fs, src_languages):
        project = project_root(fs)
        images = build_images(project, [('share', 'assets/share/a/X.png')])
        assets = AssetAll(list(images))
        assert assets.total == 1
        assert list(assets.dict_module) == ['a']

    def test_unexpected_files(self):
        with pytest.raises(ValueError, match='Unexpected files given to AssetAll'):
            AssetAll(1)

    def test_load_all(self, fs, src_languages, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12))
        write_png(fs, project.joinpath('assets/share/b/Y.png'), area=(1, 1, 9, 9))
        assets = AssetAll(
            build_images(
                project,
                [
                    ('share', 'assets/share/a/X.png'),
                    ('share', 'assets/share/b/Y.png'),
                ],
            )
        )
        assets.load()
        assert [(module.module, frame.area) for module in assets for asset in module for frame in asset] == [
            ('a', (5, 4, 15, 12)),
            ('b', (1, 1, 9, 9)),
        ]

    def test_load_modules(self, fs, src_languages, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12))
        write_png(fs, project.joinpath('assets/share/b/Y.png'), area=(1, 1, 9, 9))
        assets = AssetAll(
            build_images(
                project,
                [
                    ('share', 'assets/share/a/X.png'),
                    ('share', 'assets/share/b/Y.png'),
                ],
            )
        )
        assets.load({'a'})
        loaded = {module.module: [frame.area for asset in module for frame in asset] for module in assets}
        assert loaded == {'a': [(5, 4, 15, 12)], 'b': [()]}


class TestAssetDataFields:
    """The defaults of AssetData"""

    def test_defaults(self):
        data = AssetData(path='assets/share/a/X.png', module='a', asset='X', lang='share', frame=0, images=[])
        assert data.area == ()
        assert data.search == ()
        assert data.color == ()
        assert data.button == ()
        assert data.has_raw_area is False
        assert data.has_raw_search is False
        assert data.has_raw_color is False
        assert data.has_raw_button is False
