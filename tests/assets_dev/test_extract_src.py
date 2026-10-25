import pytest

from alasio.assets_dev.extract_src import AssetsExtractorSRC
from alasio.base.op import Area
from alasio.codegen.python import CodeGen
from alasio.config.const import Const
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.assets_dev.helpers import project_root, write_png


class TestPatchConst:
    """patch_const() switches the constants to the layout of StarRailCopilot"""

    def test_patch_const(self, fs):
        AssetsExtractorSRC(project_root(fs)).patch_const()
        assert Const.ASSETS_PATH == 'assets'
        assert Const.ASSETS_MODULE == 'tasks'
        assert list(Const.ASSETS_LANG) == ['share', 'cn', 'en']


class TestPopulate:
    """populate() adds the './' prefix, generates search and copies attributes of the first frame"""

    def test_populate(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12))
        write_png(fs, project.joinpath('assets/share/a/X.2.png'), area=(6, 5, 14, 11))
        extractor = AssetsExtractorSRC(project)
        asset = extractor.assets.dict_module['a'].dict_assets['X']
        extractor.assets.load({'a'})
        extractor.populate()

        first, second = asset.dict_lang_frame['share'].values()
        # the path is relative to the repository root, and starts with './'
        assert first.path == './assets/share/a/X.png'
        assert second.path == './assets/share/a/X.2.png'
        # search is generated from area
        assert isinstance(first.search, Area)
        assert first.search == (0, 0, 35, 20)
        assert second.search == (0, 0, 34, 20)

    def test_populate_is_idempotent(self, fs, small_resolution):
        """populate() may be called again, the './' prefix is not added twice"""
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12))
        extractor = AssetsExtractorSRC(project)
        asset = extractor.assets.dict_module['a'].dict_assets['X']
        extractor.assets.load({'a'})
        extractor.populate()
        extractor.populate()

        frame = asset.dict_lang_frame['share'][0]
        assert frame.path == './assets/share/a/X.png'
        assert frame.search == (0, 0, 35, 20)

    def test_copy_attributes_from_first_frame(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'), area=(5, 4, 15, 12))
        write_png(fs, project.joinpath('assets/share/a/X.area.png'), area=(1, 2, 20, 18))
        write_png(fs, project.joinpath('assets/share/a/X.2.png'), area=(6, 5, 14, 11))
        extractor = AssetsExtractorSRC(project)
        asset = extractor.assets.dict_module['a'].dict_assets['X']
        extractor.assets.load({'a'})
        extractor.populate()

        first, second = asset.dict_lang_frame['share'].values()
        assert first.area == (1, 2, 20, 18)
        # the raw area of the first frame applies to the second frame
        assert second.area == (1, 2, 20, 18)
        assert second.has_raw_area is False


class TestGetOutputFile:
    @pytest.mark.parametrize(
        "module, expected",
        [
            ('combat', 'tasks/combat/assets/assets_combat.py'),
            ('combat/interact', 'tasks/combat/assets/assets_combat_interact.py'),
        ],
    )
    def test_output_file(self, fs, module, expected):
        project = project_root(fs)
        extractor = AssetsExtractorSRC(project)
        extractor.patch_const()
        assert extractor.get_output_file(module) == project.joinpath(expected)


class TestFileToModule:
    @pytest.mark.parametrize(
        "relative, module",
        [
            ('assets/share/combat/interact/X.png', 'combat/interact'),
            ('assets/cn/a/X.png', 'a'),
            ('assets/en/a/b/c/X.png', 'a/b/c'),
        ],
    )
    def test_file_to_module(self, fs, relative, module):
        project = project_root(fs)
        extractor = AssetsExtractorSRC(project)
        extractor.patch_const()
        assert extractor.file_to_module(project.joinpath(relative)) == module


class TestGenAsset:
    """gen_asset() writes a ButtonWrapper of Button or of a list of frames"""

    def prepare(self, fs, project, files):
        for relative, kwargs in files:
            write_png(fs, project.joinpath(relative), **kwargs)
        extractor = AssetsExtractorSRC(project)
        extractor.patch_const()
        assets = extractor.assets
        assets.load({'a'})
        extractor.populate()
        return extractor, assets

    def test_frames(self, fs, small_resolution):
        """An asset with several frames is a list of Button"""
        project = project_root(fs)
        extractor, assets = self.prepare(
            fs,
            project,
            [
                ('assets/share/a/X.png', {}),
                ('assets/share/a/X.2.png', dict(area=(6, 5, 14, 11), rgb=(10, 20, 30))),
            ],
        )
        gen = CodeGen()
        extractor.gen_asset(gen, assets.dict_module['a'].dict_assets['X'])
        assert gen.generate_str() == """\
X = ButtonWrapper(
    name='X',
    share=[
        Button(
            file='./assets/share/a/X.png',
            area=(5, 4, 15, 12),
            search=(0, 0, 35, 20),
            color=(255, 255, 255),
            button=(5, 4, 15, 12),
        ),
        Button(
            file='./assets/share/a/X.2.png',
            area=(6, 5, 14, 11),
            search=(0, 0, 34, 20),
            color=(10, 20, 30),
            button=(6, 5, 14, 11),
        ),
    ],
)
"""

    def test_missing_language(self, fs, small_resolution):
        """An asset without share needs every language, a missing one is left as None"""
        project = project_root(fs)
        extractor, assets = self.prepare(fs, project, [('assets/cn/a/Y.png', {})])
        gen = CodeGen()
        extractor.gen_asset(gen, assets.dict_module['a'].dict_assets['Y'])
        assert gen.generate_str() == """\
Y = ButtonWrapper(
    name='Y',
    cn=Button(
        file='./assets/cn/a/Y.png',
        area=(5, 4, 15, 12),
        search=(0, 0, 35, 20),
        color=(255, 255, 255),
        button=(5, 4, 15, 12),
    ),
    en=None,
)
"""

    def test_raw_search(self, fs, small_resolution):
        """A raw search image is used as it is, not generated from area"""
        project = project_root(fs)
        extractor, assets = self.prepare(
            fs,
            project,
            [
                ('assets/share/a/Z.png', {}),
                ('assets/share/a/Z.search.png', dict(area=(7, 6, 17, 14))),
            ],
        )
        gen = CodeGen()
        extractor.gen_asset(gen, assets.dict_module['a'].dict_assets['Z'])
        assert gen.generate_str() == """\
Z = ButtonWrapper(
    name='Z',
    share=Button(
        file='./assets/share/a/Z.png',
        area=(5, 4, 15, 12),
        search=(7, 6, 17, 14),
        color=(255, 255, 255),
        button=(5, 4, 15, 12),
    ),
)
"""


class TestGenerate:
    """generate() writes one file per module and removes the stale files"""

    def test_write(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'))
        write_png(fs, project.joinpath('assets/share/a/X.2.png'), area=(6, 5, 14, 11), rgb=(10, 20, 30))
        write_png(fs, project.joinpath('assets/cn/a/Y.png'))
        write_png(fs, project.joinpath('assets/share/a/Z.png'))
        write_png(fs, project.joinpath('assets/share/a/Z.search.png'), area=(7, 6, 17, 14))
        init = project.joinpath('tasks/a/assets/__init__.py')
        fs.create_file(init, contents='')
        output = project.joinpath('tasks/a/assets/assets_a.py')

        with logger.mock_capture_writer() as capture:
            AssetsExtractorSRC(project).generate()

        assert output.atomic_read_text() == """\
from module.base.button import Button, ButtonWrapper


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m dev_tools.button_extract ```

X = ButtonWrapper(
    name='X',
    share=[
        Button(
            file='./assets/share/a/X.png',
            area=(5, 4, 15, 12),
            search=(0, 0, 35, 20),
            color=(255, 255, 255),
            button=(5, 4, 15, 12),
        ),
        Button(
            file='./assets/share/a/X.2.png',
            area=(6, 5, 14, 11),
            search=(0, 0, 34, 20),
            color=(10, 20, 30),
            button=(6, 5, 14, 11),
        ),
    ],
)
Y = ButtonWrapper(
    name='Y',
    cn=Button(
        file='./assets/cn/a/Y.png',
        area=(5, 4, 15, 12),
        search=(0, 0, 35, 20),
        color=(255, 255, 255),
        button=(5, 4, 15, 12),
    ),
    en=None,
)
Z = ButtonWrapper(
    name='Z',
    share=Button(
        file='./assets/share/a/Z.png',
        area=(5, 4, 15, 12),
        search=(7, 6, 17, 14),
        color=(255, 255, 255),
        button=(5, 4, 15, 12),
    ),
)
"""
        assert init.exists() is True
        assert capture.concat() == f'INFO | Assets generate\nINFO | Write file {output}\n'

    def test_remove_stale_file(self, fs, small_resolution):
        """The generated file of a module which doesn't exist anymore is removed"""
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'))
        stale = project.joinpath('tasks/a/assets/assets_a_old.py')
        fs.create_file(stale, contents='# stale')
        init = project.joinpath('tasks/a/assets/__init__.py')
        fs.create_file(init, contents='')

        with logger.mock_capture_writer() as capture:
            AssetsExtractorSRC(project).generate()

        assert stale.exists() is False
        assert init.exists() is True
        assert capture.concat() == f"""\
INFO | Assets generate
INFO | Write file {project.joinpath("tasks/a/assets/assets_a.py")}
INFO | Remove file: {stale}
"""

    @pytest.mark.parametrize("modules", ['b', ['b']], ids=['str', 'list'])
    def test_generate_given_modules(self, fs, small_resolution, modules):
        """A change in one module regenerates that module only"""
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/share/a/X.png'))
        write_png(fs, project.joinpath('assets/share/b/Y.png'))
        extractor = AssetsExtractorSRC(project)
        extractor.generate()
        other = project.joinpath('tasks/b/assets/assets_b.py')
        other_content = other.atomic_read_text()
        current = project.joinpath('tasks/a/assets/assets_a.py')
        current_content = current.atomic_read_text()

        # both modules changed, only module b is generated
        write_png(fs, project.joinpath('assets/share/b/Y.2.png'))
        write_png(fs, project.joinpath('assets/share/a/X.2.png'))
        with logger.mock_capture_writer() as capture:
            extractor.generate(modules=modules)

        assert capture.concat() == f"""\
INFO | Assets generate, modules={{'b'}}
INFO | Write file {other}
"""
        assert other.atomic_read_text() != other_content
        assert current.atomic_read_text() == current_content
