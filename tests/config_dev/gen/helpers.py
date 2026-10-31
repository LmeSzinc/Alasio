"""
Shared helpers of the ConfigGenerator tests.

A test describes only the yaml it generates from, the helpers create the nav
folder on the in-memory filesystem and build the generator. The generated
files are organized by the file name, so the helpers have one builder per
entry type instead of one per tested method.
"""
import json

from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen.gen_config import ConfigGenerator
from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.ext.path import PathStr
from alasio.ext.path.calc import joinpath


class GitAddSpy:
    """
    Stand-in of alasio.git.stage.gitadd.GitAdd, records the staged files.
    """

    def __init__(self):
        self.files = []

    def stage_add(self, file):
        """
        Args:
            file (str): File to stage
        """
        self.files.append(str(file))


def make_entry(fs, name='test', languages=('zh-CN', 'en-US')):
    """
    Build the ModEntryInfo of the navs generated on the fake filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        name (str): Mod name
        languages (tuple[str]): gui_language of the entry

    Returns:
        ModEntryInfo:
    """
    return ModEntryInfo(
        name=name,
        root=fs.root_dir.path,
        path_config='module/config',
        gui_language=dict.fromkeys(languages),
    )


def nav_dir(fs, nav):
    """
    Absolute path of a nav folder of the mod on the fake filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        nav (str): Nav name

    Returns:
        str:
    """
    return joinpath(fs.root_dir.path, f'module/config/{nav}')


def make_generator(fs, nav='testnav', yaml='', *, entry=None, languages=('zh-CN', 'en-US'),
                   model=None, i18n=None, tasks=None):
    """
    Build a ConfigGenerator of a mod nav on the fake filesystem.

    Every argument but `yaml` is optional, a file is created only when its
    content is given. Pass `yaml=None` to build a second generator over the
    files a previous generator has written, without touching them.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        nav (str): Nav name
        yaml (str | None): Content of {nav}.args.yaml, None to keep the existing file
        entry (ModEntryInfo, optional): Entry of the mod, default to a new test entry
        languages (tuple[str]): gui_language of the default entry
        model (str, optional): Content of the existing {nav}_model.py
        i18n (dict, optional): Content of the existing {nav}_i18n.json
        tasks (str, optional): Content of the existing {nav}.tasks.yaml

    Returns:
        ConfigGenerator:
    """
    if entry is None:
        entry = make_entry(fs, languages=languages)
    folder = nav_dir(fs, nav)
    file = joinpath(folder, f'{nav}.args.yaml')
    if yaml is not None:
        fs.create_file(file, contents=yaml)
    if model is not None:
        fs.create_file(joinpath(folder, f'{nav}_model.py'), contents=model)
    if i18n is not None:
        fs.create_file(joinpath(folder, f'{nav}_i18n.json'), contents=json.dumps(i18n, ensure_ascii=False))
    if tasks is not None:
        fs.create_file(joinpath(folder, f'{nav}.tasks.yaml'), contents=tasks)
    gen = ConfigGenerator(entry, PathStr.new(file))
    gen.folder = nav
    return gen


def make_alasio_generator(fs, nav='device', yaml=''):
    """
    Build a ConfigGenerator of the alasio entry itself on the fake filesystem.

    The nav of the alasio entry imports the group classes from group_base
    instead of the generated group_export, and it has no mod to cross-check.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        nav (str): Nav name
        yaml (str): Content of {nav}.args.yaml

    Returns:
        ConfigGenerator:
    """
    entry = ModEntryInfo.alasio()
    file = joinpath(fs.root_dir.path, f'alasio/config/{nav}/{nav}.args.yaml')
    fs.create_file(file, contents=yaml)
    gen = ConfigGenerator(entry, PathStr.new(file))
    gen.folder = nav
    return gen


def make_cross(fs, navs, tasks=None, generator_class=None):
    """
    Build a cross-nav generator over several navs of the mod, with the MRO built.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        navs (dict[str, str]): key: nav name, value: content of {nav}.args.yaml
        tasks (dict[str, str], optional): key: nav name, value: content of {nav}.tasks.yaml
        generator_class (type, optional): Class to instantiate, default to
            CrossNavGenerator

    Returns:
        CrossNavGenerator: Generator with the MRO built and validated
    """
    if generator_class is None:
        generator_class = CrossNavGenerator
    entry = make_entry(fs)
    for nav, yaml in navs.items():
        fs.create_file(joinpath(nav_dir(fs, nav), f'{nav}.args.yaml'), contents=yaml)
    for nav, yaml in (tasks or {}).items():
        fs.create_file(joinpath(nav_dir(fs, nav), f'{nav}.tasks.yaml'), contents=yaml)
    gen = generator_class(entry)
    gen.build_group_mro()
    gen.validate_nav_model_parents()
    return gen
