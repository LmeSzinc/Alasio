import pytest

from alasio.assets_dev.extract_alas import AssetsExtractorALAS
from alasio.codegen.python import CodeGen
from alasio.config.const import Const
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.assets_dev.helpers import build_multilang, project_root, write_png


class TestPatchConst:
    """patch_const() switches the constants to the layout of AzurLaneAutoScript"""

    def test_patch_const(self, fs):
        AssetsExtractorALAS(project_root(fs)).patch_const()
        assert Const.ASSETS_PATH == 'assets'
        assert Const.ASSETS_MODULE == 'module'
        assert list(Const.ASSETS_LANG) == ['cn', 'en', 'jp', 'tw']


class TestPopulate:
    """Alas has no populate, search is not generated and the path stays repo relative"""

    def test_populate(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        extractor = AssetsExtractorALAS(project)
        asset = extractor.assets.dict_module['a'].dict_assets['X']
        extractor.assets.load({'a'})
        extractor.populate()
        frame = asset.dict_lang_frame['cn'][0]
        assert frame.area == (5, 4, 15, 12)
        assert frame.search == ()
        # gen_asset adds the './' prefix, the path is the bbox coordinate system
        assert frame.path == 'assets/cn/a/X.png'


class TestGetOutputFile:
    """Alas has one assets.py per top level module"""

    @pytest.mark.parametrize(
        "module, expected",
        [
            ('combat', 'module/combat/assets.py'),
            ('combat/support', 'module/combat/assets.py'),
        ],
    )
    def test_output_file(self, fs, module, expected):
        project = project_root(fs)
        extractor = AssetsExtractorALAS(project)
        extractor.patch_const()
        assert extractor.get_output_file(module) == project.joinpath(expected)


class TestFileToModule:
    @pytest.mark.parametrize(
        "relative, module",
        [
            ('assets/cn/combat/X.png', 'combat'),
            ('assets/tw/combat_ui/X.png', 'combat_ui'),
        ],
    )
    def test_file_to_module(self, fs, relative, module):
        project = project_root(fs)
        extractor = AssetsExtractorALAS(project)
        extractor.patch_const()
        assert extractor.file_to_module(project.joinpath(relative)) == module


class TestIsKeepFile:
    """Only the generated assets.py is removed from the module folder"""

    @pytest.mark.parametrize(
        "file, keep",
        [
            ('module/combat/assets.py', False),
            ('module/combat/combat.py', True),
            # the module folder holds hand written files too
            ('module/combat/utils_assets.py', True),
            ('module/combat/__init__.py', True),
        ],
    )
    def test_keep_file(self, file, keep):
        assert AssetsExtractorALAS.is_keep_file(file) is keep


class TestGenAsset:
    """gen_asset() writes one line of Button or Template per asset"""

    def test_button(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        extractor = AssetsExtractorALAS(project)
        asset = build_multilang(project, [('cn', 'assets/cn/a/X.png')])
        extractor.assets.load({'a'})
        for frame in asset:
            frame.load()

        gen = CodeGen()
        extractor.gen_asset(gen, asset)
        assert gen.generate_str() == """\
X = Button(area={'cn': (5, 4, 15, 12), 'en': (5, 4, 15, 12), 'jp': (5, 4, 15, 12), 'tw': (5, 4, 15, 12)}, color={'cn': (255, 255, 255), 'en': (255, 255, 255), 'jp': (255, 255, 255), 'tw': (255, 255, 255)}, button={'cn': (5, 4, 15, 12), 'en': (5, 4, 15, 12), 'jp': (5, 4, 15, 12), 'tw': (5, 4, 15, 12)}, file={'cn': './assets/cn/a/X.png', 'en': './assets/cn/a/X.png', 'jp': './assets/cn/a/X.png', 'tw': './assets/cn/a/X.png'})
"""

    def test_button_attribute(self, fs, small_resolution):
        """A language without its own image falls back to the cn image of the same asset"""
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        write_png(fs, project.joinpath('assets/cn/a/X.button.png'), area=(2, 3, 30, 15))
        write_png(fs, project.joinpath('assets/en/a/X.png'), area=(4, 3, 16, 13), rgb=(10, 20, 30))
        extractor = AssetsExtractorALAS(project)
        asset = build_multilang(
            project,
            [
                ('cn', 'assets/cn/a/X.png'),
                ('cn', 'assets/cn/a/X.button.png'),
                ('en', 'assets/en/a/X.png'),
            ],
        )
        for lang in asset.dict_lang_frame.values():
            for frame in lang.values():
                frame.load()

        gen = CodeGen()
        extractor.gen_asset(gen, asset)
        assert gen.generate_str() == """\
X = Button(area={'cn': (5, 4, 15, 12), 'en': (4, 3, 16, 13), 'jp': (5, 4, 15, 12), 'tw': (5, 4, 15, 12)}, color={'cn': (255, 255, 255), 'en': (10, 20, 30), 'jp': (255, 255, 255), 'tw': (255, 255, 255)}, button={'cn': (2, 3, 30, 15), 'en': (4, 3, 16, 13), 'jp': (2, 3, 30, 15), 'tw': (2, 3, 30, 15)}, file={'cn': './assets/cn/a/X.png', 'en': './assets/en/a/X.png', 'jp': './assets/cn/a/X.png', 'tw': './assets/cn/a/X.png'})
"""

    def test_template(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/TEMPLATE_T.png'), width=20, height=10, area=(1, 1, 5, 6))
        write_png(fs, project.joinpath('assets/en/a/TEMPLATE_T.png'), width=20, height=10, area=(1, 1, 5, 6))
        extractor = AssetsExtractorALAS(project)
        asset = build_multilang(
            project,
            [
                ('cn', 'assets/cn/a/TEMPLATE_T.png'),
                ('en', 'assets/en/a/TEMPLATE_T.png'),
            ],
        )
        for lang in asset.dict_lang_frame.values():
            for frame in lang.values():
                frame.load()

        gen = CodeGen()
        extractor.gen_asset(gen, asset)
        # a template has no area, color and button
        assert gen.generate_str() == """\
TEMPLATE_T = Template(file={'cn': './assets/cn/a/TEMPLATE_T.png', 'en': './assets/en/a/TEMPLATE_T.png', 'jp': './assets/cn/a/TEMPLATE_T.png', 'tw': './assets/cn/a/TEMPLATE_T.png'})
"""


class TestGenerate:
    """generate() writes the assets.py of a module and keeps the other files of the folder"""

    def test_write(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        write_png(fs, project.joinpath('assets/en/a/X.png'))
        write_png(fs, project.joinpath('assets/cn/a/TEMPLATE_T.png'), width=20, height=10, area=(1, 1, 5, 6))
        write_png(fs, project.joinpath('assets/en/a/TEMPLATE_T.png'), width=20, height=10, area=(1, 1, 5, 6))
        hand_written = project.joinpath('module/a/utils_assets.py')
        fs.create_file(hand_written, contents='# hand written')
        output = project.joinpath('module/a/assets.py')

        with logger.mock_capture_writer() as capture:
            AssetsExtractorALAS(project).generate()

        assert output.atomic_read_text() == """\
from module.base.button import Button
from module.base.template import Template

# This file was automatically generated by dev_tools/button_extract.py.
# Don't modify it manually.

TEMPLATE_T = Template(file={'cn': './assets/cn/a/TEMPLATE_T.png', 'en': './assets/en/a/TEMPLATE_T.png', 'jp': './assets/cn/a/TEMPLATE_T.png', 'tw': './assets/cn/a/TEMPLATE_T.png'})
X = Button(area={'cn': (5, 4, 15, 12), 'en': (5, 4, 15, 12), 'jp': (5, 4, 15, 12), 'tw': (5, 4, 15, 12)}, color={'cn': (255, 255, 255), 'en': (255, 255, 255), 'jp': (255, 255, 255), 'tw': (255, 255, 255)}, button={'cn': (5, 4, 15, 12), 'en': (5, 4, 15, 12), 'jp': (5, 4, 15, 12), 'tw': (5, 4, 15, 12)}, file={'cn': './assets/cn/a/X.png', 'en': './assets/en/a/X.png', 'jp': './assets/cn/a/X.png', 'tw': './assets/cn/a/X.png'})
"""
        assert hand_written.exists() is True
        assert capture.concat() == f"""\
INFO | Assets generate
INFO | Write file {output}
"""

    def test_remove_stale_assets(self, fs, small_resolution):
        """The assets.py of a removed module is removed, the other files of it are kept"""
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        stale = project.joinpath('module/a2/assets.py')
        fs.create_file(stale, contents='# stale')
        hand_written = project.joinpath('module/a2/utils_assets.py')
        fs.create_file(hand_written, contents='# hand written')

        with logger.mock_capture_writer() as capture:
            AssetsExtractorALAS(project).generate(modules={'a2'})

        assert stale.exists() is False
        assert hand_written.exists() is True
        assert capture.concat() == f"""\
INFO | Assets generate, modules={{'a2'}}
INFO | Remove file: {stale}
"""
