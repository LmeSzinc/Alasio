import pytest

import alasio.assets_dev.extract as extract_module
from alasio.assets_dev.extract import AssetsExtractor
from alasio.ext.file.watchdog import PathEvent
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.assets_dev.helpers import project_root, write_png


class TestIsAssetsFile:
    """is_assets_file() checks the extension and that the name can be a python variable"""

    @pytest.mark.parametrize(
        "file",
        [
            'X.png',
            'X.gif',
            'X1.png',
            'X_1.png',
            'TEMPLATE_X.png',
            'X.2.png',
            'X.search.png',
            'X.2.search.png',
            # only the part before the first dot is the asset name
            'X.中文.png',
            '/project/assets/cn/combat/X.png',
        ],
    )
    def test_accept(self, file):
        assert AssetsExtractor.is_assets_file(file) is True

    @pytest.mark.parametrize(
        "file, reason",
        [
            ('X.jpg', 'jpg is not an asset extension'),
            ('X.PNG', 'the extension is case sensitive'),
            ('X', 'no extension'),
            ('.png', 'no asset name'),
            ('.gitkeep', 'no asset extension'),
            ('X-Y.png', 'dash is not allowed in a python variable name'),
            ('X Y.png', 'space is not allowed in a python variable name'),
            ('1X.png', 'a python variable can not start with a digit'),
            ('X!.png', 'only word characters are allowed'),
            ('Screenshot_20250520-212905.png', 'LDPlayer screenshot'),
            ('MuMu12-20250521-072111.png', 'MuMu 12 screenshot'),
            ('MuMu20190305233638.png', 'MuMu 6 screenshot'),
            ('NemuPlayer 2019-03-04 18-48-16-52.png', 'NemuPlayer screenshot'),
        ],
    )
    def test_reject(self, file, reason):
        assert AssetsExtractor.is_assets_file(file) is False, reason


class TestGetOutputFile:
    """get_output_file() puts the generated file under the folder of the top level module"""

    @pytest.mark.parametrize(
        "module, expected",
        [
            ('combat', 'tasks/combat/assets/assets_combat.py'),
            ('combat/interact', 'tasks/combat/assets/assets_combat_interact.py'),
            ('a/b/c', 'tasks/a/assets/assets_a_b_c.py'),
        ],
    )
    def test_output_file(self, fs, module, expected):
        project = project_root(fs)
        assert AssetsExtractor(project).get_output_file(module) == project.joinpath(expected)


class TestIsKeepFile:
    """is_keep_file() keeps only __init__.py, every other .py file is generated"""

    @pytest.mark.parametrize(
        "file, keep",
        [
            ('tasks/a/assets/__init__.py', True),
            ('tasks/a/assets/assets_a.py', False),
            ('tasks/a/assets/assets_a_b.py', False),
        ],
    )
    def test_keep_file(self, file, keep):
        assert AssetsExtractor.is_keep_file(file) is keep


class TestFileToModule:
    """file_to_module() strips the assets folder and the language folder"""

    @pytest.mark.parametrize(
        "relative, module",
        [
            ('assets/cn/combat/X.png', 'combat'),
            ('assets/cn/combat/interact/X.png', 'combat/interact'),
            ('assets/en/a/b/c/X.png', 'a/b/c'),
            # a module named like a language is not stripped twice
            ('assets/cn/cn/X.png', 'cn'),
        ],
    )
    def test_file_to_module(self, fs, relative, module):
        project = project_root(fs)
        assert AssetsExtractor(project).file_to_module(project.joinpath(relative)) == module


class TestAssets:
    """assets reads the assets folder and builds the module / asset / frame structure"""

    def test_build(self, fs):
        project = project_root(fs)
        folder = project.joinpath('assets/cn/a')
        write_png(fs, folder.joinpath('X.png'))
        write_png(fs, folder.joinpath('X.2.png'))
        write_png(fs, folder.joinpath('X.2.search.png'))
        write_png(fs, folder.joinpath('Y.png'))
        write_png(fs, project.joinpath('assets/en/a/X.png'))
        # not assets, ignored
        fs.create_file(folder.joinpath('notes.txt'), contents='')
        fs.create_file(folder.joinpath('Screenshot_20250520-212905.png'), contents=b'')
        fs.create_file(folder.joinpath('1X.png'), contents=b'')

        assets = AssetsExtractor(project).assets
        assert assets.total == 5
        assert str(assets) == 'AssetAll(modules=1, total=5)'
        assert list(assets.dict_module) == ['a']

        module = assets.dict_module['a']
        assert list(module.dict_assets) == ['X', 'Y']
        x = module.dict_assets['X']
        # every language is present, in the order of Const.ASSETS_LANG
        assert list(x.dict_lang_frame) == ['cn', 'en', 'jp', 'tw']
        assert list(x.dict_lang_frame['cn']) == [0, 2]
        assert list(x.dict_lang_frame['en']) == [0]
        assert x.dict_lang_frame['jp'] == {}
        assert x.dict_lang_frame['tw'] == {}
        # the language of an image is the language folder, not the filename
        assert x.dict_lang_frame['cn'][0].lang == 'cn'
        assert x.dict_lang_frame['en'][0].lang == 'en'
        assert x.dict_lang_frame['cn'][0].path == 'assets/cn/a/X.png'

    def test_cached(self, fs):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        extractor = AssetsExtractor(project)
        assert extractor.assets is extractor.assets


class TestGenerate:
    """generate() writes the assets of every module and removes stale files"""

    def test_write(self, fs, small_resolution):
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        output = project.joinpath('tasks/a/assets/assets_a.py')

        with logger.mock_capture_writer() as capture:
            AssetsExtractor(project).generate()

        assert output.atomic_read_text() == """\
from module.base.button import Button, ButtonWrapper


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m dev_tools.button_extract ```

X = ButtonWrapper(
    name='X',
    cn=Button(
        file='assets/cn/a/X.png',
        area=(5, 4, 15, 12),
        search=(0, 0, 35, 20),
        color=(255, 255, 255),
        button=(5, 4, 15, 12),
    ),
    en=None,
    jp=None,
    tw=None,
)
"""
        assert capture.concat() == f'INFO | Assets generate\nINFO | Write file {output}\n'

    def test_skip_same(self, fs, small_resolution):
        """The second generate of the same assets doesn't write the file again"""
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        extractor = AssetsExtractor(project)
        extractor.generate()

        with logger.mock_capture_writer() as capture:
            extractor.generate()
        assert capture.concat() == 'INFO | Assets generate\n'

    def test_remove_stale_file(self, fs, small_resolution):
        """A generated file of a module which doesn't exist anymore is removed"""
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        stale = project.joinpath('tasks/a/assets/assets_a_old.py')
        fs.create_file(stale, contents='# stale')
        init = project.joinpath('tasks/a/assets/__init__.py')
        fs.create_file(init, contents='')

        with logger.mock_capture_writer() as capture:
            AssetsExtractor(project).generate()

        assert stale.exists() is False
        assert init.exists() is True
        assert capture.concat() == f"""\
INFO | Assets generate
INFO | Write file {project.joinpath("tasks/a/assets/assets_a.py")}
INFO | Remove file: {stale}
"""

    def test_remove_empty_folder(self, fs, small_resolution):
        """The folder of a removed module is removed, __pycache__ is ignored"""
        project = project_root(fs)
        folder = project.joinpath('tasks/a/assets')
        fs.create_file(folder.joinpath('assets_a_old.py'), contents='# stale')
        fs.create_file(folder.joinpath('__pycache__/assets_a_old.cpython-38.pyc'), contents=b'')

        with logger.mock_capture_writer() as capture:
            AssetsExtractor(project).generate(modules={'a/old'})

        assert folder.exists() is False
        assert capture.concat() == f"""\
INFO | Assets generate, modules={{'a/old'}}
INFO | Remove file: {folder.joinpath("assets_a_old.py")}
INFO | Remove empty folder: {folder}
"""

    def test_generate_given_modules(self, fs, small_resolution):
        """Generating some modules must not remove the files of the other modules"""
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        write_png(fs, project.joinpath('assets/cn/b/Y.png'))
        extractor = AssetsExtractor(project)
        extractor.generate()
        other = project.joinpath('tasks/b/assets/assets_b.py')
        other_content = other.atomic_read_text()

        # a new asset of module a, nothing changed in module b
        write_png(fs, project.joinpath('assets/cn/a/Z.png'))
        with logger.mock_capture_writer() as capture:
            extractor.generate(modules='a')

        assert capture.concat() == f"""\
INFO | Assets generate, modules={{'a'}}
INFO | Write file {project.joinpath("tasks/a/assets/assets_a.py")}
"""
        assert other.atomic_read_text() == other_content

    def test_gitadd(self, fs, small_resolution):
        """Every written and every removed file is staged"""
        project = project_root(fs)
        write_png(fs, project.joinpath('assets/cn/a/X.png'))
        stale = project.joinpath('tasks/a/assets/assets_a_old.py')
        fs.create_file(stale, contents='# stale')

        class FakeGitAdd:
            def __init__(self):
                self.added = []

            def stage_add(self, file):
                self.added.append(PathStr.new(file))

        gitadd = FakeGitAdd()
        AssetsExtractor(project).generate(gitadd=gitadd)
        assert gitadd.added == [project.joinpath('tasks/a/assets/assets_a.py'), stale]


class TestGenerateOnChange:
    """generate_on_change() maps the changed files to their module and regenerates it"""

    @pytest.fixture
    def generated(self, fs, monkeypatch):
        """Record the generate() calls of an extractor"""
        project = project_root(fs)
        extractor = AssetsExtractor(project)
        calls = []
        monkeypatch.setattr(extractor, 'generate', lambda modules=None, gitadd=None: calls.append((modules, gitadd)))
        return project, extractor, calls

    def test_module_of_events(self, generated):
        project, extractor, calls = generated
        events = [
            PathEvent('M', str(project.joinpath('assets/cn/combat/X.png')), 'F', 0),
            PathEvent('M', str(project.joinpath('assets/cn/combat/interact/X.2.png')), 'F', 0),
            PathEvent('M', str(project.joinpath('assets/en/combat/X.png')), 'F', 0),
        ]
        extractor.generate_on_change(events)
        # the modules of all events, generated once
        assert calls == [({'combat', 'combat/interact'}, None)]

    def test_ignores_not_assets(self, generated):
        project, extractor, calls = generated
        events = [
            PathEvent('M', str(project.joinpath('assets/cn/combat/notes.txt')), 'F', 0),
            PathEvent('M', str(project.joinpath('assets/cn/combat/Screenshot_20250520-212905.png')), 'F', 0),
        ]
        extractor.generate_on_change(events)
        assert calls == []

    def test_no_events(self, generated):
        project, extractor, calls = generated
        extractor.generate_on_change([])
        assert calls == []

    def test_gitadd(self, generated):
        project, extractor, calls = generated
        gitadd = object()
        events = [PathEvent('M', str(project.joinpath('assets/cn/a/X.png')), 'F', 0)]
        extractor.generate_on_change(events, gitadd=gitadd)
        assert calls == [({'a'}, gitadd)]


class TestImageFixup:
    """image_fixup() re-encodes a new asset png, and ignores everything else"""

    @staticmethod
    def event(file):
        return PathEvent('M', str(file), 'F', 0)

    def test_fixup(self, fs):
        project = project_root(fs)
        file = project.joinpath('assets/cn/a/X.png')
        content = write_png(fs, file, compress=0)

        with logger.mock_capture_writer() as capture:
            changed = AssetsExtractor(project).image_fixup(self.event(file))

        assert changed is True
        assert capture.concat() == f'INFO | Fixup image: {file}\n'
        # the file has been encoded again
        assert file.atomic_read_bytes() != content

    def test_fixup_same(self, fs):
        """A file written by image_fixup has nothing to fixup"""
        project = project_root(fs)
        file = project.joinpath('assets/cn/a/X.png')
        write_png(fs, file, compress=9)

        with logger.mock_capture_writer() as capture:
            changed = AssetsExtractor(project).image_fixup(self.event(file))

        assert changed is False
        assert capture.concat() == ''

    def test_not_png(self, fs):
        project = project_root(fs)
        file = project.joinpath('assets/cn/a/X.gif')
        fs.create_file(file, contents=b'')
        assert AssetsExtractor(project).image_fixup(self.event(file)) is False

    def test_not_an_asset(self, fs):
        project = project_root(fs)
        file = project.joinpath('assets/cn/a/Screenshot_20250520-212905.png')
        write_png(fs, file)
        assert AssetsExtractor(project).image_fixup(self.event(file)) is False

    def test_full_screenshot(self, fs):
        """A full screenshot is not cropped, need_crop skips it"""
        project = project_root(fs)
        file = project.joinpath('assets/cn/a/X.png')
        write_png(fs, file, area=(0, 0, 40, 20), compress=0)
        assert AssetsExtractor(project).image_fixup(self.event(file)) is False

    def test_broken_image(self, fs):
        project = project_root(fs)
        file = project.joinpath('assets/cn/a/X.png')
        fs.create_file(file, contents=b'broken')
        assert AssetsExtractor(project).image_fixup(self.event(file)) is False

    def test_missing_file(self, fs):
        project = project_root(fs)
        assert AssetsExtractor(project).image_fixup(self.event(project.joinpath('assets/cn/a/X.png'))) is False


class TestWatchFiles:
    """watch_files() stages new assets, fixes them up and regenerates their modules"""

    @staticmethod
    def prepare(fs, monkeypatch, events, created):
        project = project_root(fs)
        extractor = AssetsExtractor(project)

        class FakeWatchdog:
            def __init__(self):
                self.marked = []

            def watch_files(self, include_existing=False, interval=2):
                return events

            def mark_modified(self, event):
                self.marked.append(event)

        class FakeGitAdd:
            def __init__(self, root):
                self.added = []
                created['gitadd'] = self

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

            def stage_add(self, file):
                self.added.append(PathStr.new(file))

        watchdog = FakeWatchdog()
        monkeypatch.setattr(extractor, 'watchdog', watchdog)
        monkeypatch.setattr(extract_module, 'GitAdd', FakeGitAdd)
        created['generated'] = []
        monkeypatch.setattr(
            extractor, 'generate_on_change',
            lambda events, gitadd=None: created['generated'].append((events, gitadd)),
        )
        return project, extractor, watchdog

    def test_watch_files(self, fs, monkeypatch):
        created = {}
        project = project_root(fs)
        asset = project.joinpath('assets/cn/a/X.png')
        notes = project.joinpath('assets/cn/a/notes.txt')
        write_png(fs, asset, compress=0)
        fs.create_file(notes, contents='')
        events = [
            PathEvent('A', str(asset), 'F', 0),
            PathEvent('A', str(notes), 'F', 0),
        ]
        project, extractor, watchdog = self.prepare(fs, monkeypatch, events, created)

        extractor.watch_files()

        # only the asset is staged
        assert created['gitadd'].added == [asset]
        # the fixed up asset is marked as modified by us
        assert watchdog.marked == [events[0]]
        # all the events are passed to the generator, it filters them itself
        assert created['generated'] == [(events, created['gitadd'])]

    def test_no_events(self, fs, monkeypatch):
        created = {}
        project, extractor, watchdog = self.prepare(fs, monkeypatch, [], created)

        extractor.watch_files()

        # no event, no git and no generation
        assert 'gitadd' not in created
        assert created['generated'] == []
