"""
The mod entry: the declaration of a mod and the location of that declaration.

A mod declares itself in ``<mod_root>/module/config/_index/mod_entry.yaml``,
a data file rather than code: the declaration is parsed with msgspec, the mod
is never imported, compiled or executed. The same reader serves every consumer
of an entry:

- the runtime (``alasio.config.entry.loader.ModLoader``): the mod list of the
  backend, the scheduler of a mod, the asset folders;
- the code generators (``alasio.config_dev``): the GUI languages, the folders
  of the task entry functions;
- the pack server (``alasio.deploy_dev.pack_server``): the declarations of a
  repo read from a git blob, where there is no working tree to import from.

The location of the file is part of the declaration, and the only reason a
mod is findable: a folder holding ``module/config/_index/mod_entry.yaml`` is
a mod root. The layout (``module/config``) is a constant, it is not declared
in the file and not configurable, see ``MOD_CONFIG``: a file cannot tell
where the file is, so the framework looks at one fixed relative path instead
of trusting the content of a file it has not read yet.

Two layers, one value:

- :class:`ModEntryModel` is the file, the keys as they are written, lists
  included, the ``help`` annotation of every field is the comment above it;
- :class:`ModEntry` is the handle: the location (root and layout) and the
  declaration read from it. It answers the fields in the shape the runtime
  expects (``dict`` for the ordered sets, ``tuple`` for the ordered lists) as
  cached properties, so a caller reads ``entry.gui_language`` and never has to
  know how the file spells it.

The library itself is not a mod found this way: its declaration is a value of
the code, see :meth:`ModEntry.alasio`.

Usage:
    from alasio.config.entry.mod_entry import ModEntry

    entry = ModEntry.create(__file__, uppath=3)
    entry.gui_language
    entry.write()
"""

import os
from copy import deepcopy
from typing import Dict, List, Union

import msgspec
import yaml
from msgspec import Meta, Struct, field
from typing_extensions import Annotated

from alasio.ext import env
from alasio.ext.cache import cached_class_property, cached_property
from alasio.ext.file.yamlfile import yaml_loads
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.path.calc import abspath, joinnormpath, normpath
from alasio.logger import logger

# default mirrors of the PyPI simple index (PEP 503): the source of the
# distributions a mod installs with pip, when they do not update by pack.
# Two groups of the public mirrors: 'cn' (the mirrors of the mainland
# network) and 'global' (the official PyPI). The groups run in parallel, the
# members of a group are tried in the declared order, see
# alasio.deploy.httpclient.probe_mirrors. A mod whose distributions come from
# another source (e.g. a private index) overrides it on its declaration
# (pypi_mirrors); the mods that keep the default share one mirror record, see
# ModEntry.pypi_record_name
DEFAULT_PYPI_MIRRORS = {
    'cn': {
        'tsinghua': 'https://pypi.tuna.tsinghua.edu.cn/simple',
        'aliyun': 'https://mirrors.aliyun.com/pypi/simple',
        'tencent': 'https://mirrors.cloud.tencent.com/pypi/simple',
    },
    'global': {
        'pypi': 'https://pypi.org/simple',
    },
}

# pypi indexes the update pack of a mod provides: the wheels of the
# dependencies a dependency file annotates with one of these indexes (the
# '# from <url>' comment uv writes) are published with the update pack, so a
# client installs them even when the index itself is unreachable. Defaults to
# the py38deps index of the python3.8 runtime the mods ship with
DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS = (
    'https://py38deps.github.io/py38deps/simple/',
)

# Default task order of the scheduler: the DSL of alasio.base.filter, a task
# runs before the tasks written after it. A mod that declares no order still
# runs, see ModScheduler
DEFAULT_SCHEDULER_PRIORITY = 'RestartDevice > RestartGame'

# Layout of the config folder of a mod, the only one the framework looks for:
# a mod is the folder holding `module/config/_index/mod_entry.yaml`. The
# layout is not configurable on purpose, a declaration cannot tell where the
# declaration is: the file is found at a fixed relative path, which is what
# lets ModEntry.create() compute the root of a mod from any file of it
MOD_CONFIG = 'module/config'

# Layout of the config folder of the library itself: the config tree of the
# library ships inside the package (`alasio/config/**`), so it is not under
# MOD_CONFIG and it is never found by a walk up, ModEntry.alasio names it
ALASIO_CONFIG = 'alasio/config'

# Name of the entry file and of the folder it lives in, relative to the config
# folder of a mod: <root>/<MOD_CONFIG>/_index/mod_entry.yaml
MOD_ENTRY_FILE = 'mod_entry.yaml'
MOD_ENTRY_FOLDER = '_index'
MOD_ENTRY_SUBPATH = f'{MOD_ENTRY_FOLDER}/{MOD_ENTRY_FILE}'

# Path of the declaration from the root of a mod: the pack server reads the
# file at this path from the git blob of a commit, where the root of the mod
# is the root of the repo, and no working tree exists to find it in
MOD_ENTRY_PATH = f'{MOD_CONFIG}/{MOD_ENTRY_SUBPATH}'

# Default dependency file of a mod, relative to the mod root, see
# ModEntryModel.requirements_file
DEFAULT_REQUIREMENTS_FILE = 'requirements.txt'

# Modules of the library that live next to it, relative to PROJECT_ROOT, the
# mods the backend loads besides the project root itself. A folder without a
# declaration is skipped, so the list is the mods the library is distributed
# with, not the mods that exist
MOD_FOLDERS = ('ExampleMod', 'AzurLaneAutoScript', 'StarRailCopilot')

# Mirrors input of an entry: a single url, name to url, group to mirrors, and
# the flat/nested mix, see
# alasio.deploy.httpclient.probe_mirrors.Mirrors.from_input()
ModMirrorInput = Union[str, Dict[str, Union[str, Dict[str, str]]]]

# An index the update pack of a mod provides: an http(s) base url, no trailing
# slash required. The urls are matched against the sources of a dependency
# file, a value the pack server cannot request would silently provide nothing,
# so the declaration is refused instead
ModProvideIndex = Annotated[str, Meta(pattern=r'^https?://\S+$')]


class ModEntryError(ValueError):
    """
    Raised when the declaration of a mod entry cannot be read.

    The message names the file (or the source name the caller gave) and the
    reason.
    """


class ModEntryModel(Struct):
    """
    The yaml model of ``mod_entry.yaml``, the declaration of a mod.

    The field names are the keys of the file, like the deploy config, and the
    ``help`` annotation of every field is the comment written above the key:
    the declaration is written from this model, so the file follows it as the
    model evolves, and a reader never writes it.

    The collections are lists, the order is meaningful (the asset languages
    are tried in the declared order), and a value may repeat: the ordered sets
    and tuples the runtime uses are the views of :class:`ModEntry`.

    ``root`` and ``path_config`` are not fields: they are facts of the
    location, owned by :class:`ModEntry`. A file that declares them is read
    without them, msgspec ignores the keys the model does not know.
    """
    # The name is required in a declaration, the default exists so the model
    # is default constructible for the writer, a read rejects the empty name
    name: Annotated[str, Meta(extra={"help": [
        "Name of the mod, the key the framework identifies it by",
        "It must be the name the updater of the mod expects",
    ]})] = ''
    path_main: Annotated[str, Meta(extra={"help": [
        "Relative path from the mod root to the scheduler",
        "The file defining class Scheduler, e.g. 'module/main.py'",
    ]})] = 'module/main.py'
    path_assets: Annotated[str, Meta(extra={"help": [
        "Relative path from the mod root to the assets folder",
    ]})] = 'assets'
    asset_ext: Annotated[List[str], Meta(extra={"help": [
        "Valid file extensions of the assets",
        "'png' is commonly used because 'jpg' is a lossy compression",
    ]})] = field(default_factory=lambda: ['.png', '.gif'])
    asset_lang: Annotated[List[str], Meta(extra={"help": [
        "Valid languages (or servers) of the assets, in the order they are tried",
        "The shared assets ('') are always tried first",
    ]})] = field(default_factory=lambda: ['cn', 'en'])
    gui_language: Annotated[List[str], Meta(extra={"help": [
        "Languages of the GUI, the generated i18n files carry these",
        "Use the tags of the frontend, e.g. 'zh-CN', 'en-US', 'ja-JP', 'zh-TW', 'es-ES'",
    ]})] = field(default_factory=lambda: ['zh-CN', 'en-US'])
    task_entry_folders: Annotated[List[str], Meta(extra={"help": [
        "Folders scanned for task entry functions, relative to the mod root",
        "Every .py file under them is parsed for task entries",
    ]})] = field(default_factory=lambda: ['module', 'tasks'])
    mirrors: Annotated[ModMirrorInput, Meta(extra={"help": [
        "Update source of the mod, the mirrors of its update server",
        "A single url, name to url, group to mirrors, or the flat/nested mix",
        "An empty value declares no update source, the mod is never checked",
    ]})] = ''
    pypi_mirrors: Annotated[ModMirrorInput, Meta(extra={"help": [
        "Mirrors of the PyPI simple index the distributions are downloaded from",
        "The same forms as 'mirrors'; remove the key to use the default mirrors",
    ]})] = field(default_factory=lambda: deepcopy(DEFAULT_PYPI_MIRRORS))
    update_provide_pypi_mirrors: Annotated[List[ModProvideIndex], Meta(extra={"help": [
        "PyPI indexes the update packs of the mod provide",
        "The wheels of the dependencies that a dependency file annotates with",
        "one of these indexes are published with the update pack",
        "Use an empty list to provide no index",
    ]})] = field(default_factory=lambda: list(DEFAULT_UPDATE_PROVIDE_PYPI_MIRRORS))
    requirements_file: Annotated[str, Meta(extra={"help": [
        "Relative path from the mod root to the requirements file of the mod",
        "Every distribution in it is pinned to an exact version",
    ]})] = DEFAULT_REQUIREMENTS_FILE
    update_pack_deps: Annotated[List[str], Meta(extra={"help": [
        "Distributions updated from the pack channel instead of pip",
        "Any case and separator style, the compare is on the PEP 503 name",
        "A distribution not declared here installs with pip",
    ]})] = field(default_factory=list)
    scheduler_priority: Annotated[str, Meta(extra={"help": [
        "Task order of the scheduler, a task runs before the tasks after it",
        "The DSL of alasio.base.filter: 'A > B > C', a line starting with '>'",
        "continues the order of the line above it",
    ]})] = DEFAULT_SCHEDULER_PRIORITY


def _parse(source, source_name=''):
    """
    Parse the yaml text of an entry and validate it with the model.

    Args:
        source (str | bytes): Content of ``module/config/_index/mod_entry.yaml``
        source_name (str): Name of the source used in the error messages,
            e.g. the path of the file or '... at commit c3'. Defaults to '',
            the message only carries the reason

    Returns:
        ModEntryModel: The parsed declaration

    Raises:
        ModEntryError: If the yaml is not valid or a value does not fit
    """
    prefix = f'{source_name}: ' if source_name else ''
    try:
        content = yaml_loads(source)
    except yaml.YAMLError as e:
        raise ModEntryError(f'{prefix}invalid yaml: {e}') from None
    if content is None:
        # An empty file declares nothing, the model reports the missing name
        content = {}
    if not isinstance(content, dict):
        raise ModEntryError(
            f'{prefix}the entry must be a mapping, got {type(content).__name__}')
    try:
        model = msgspec.convert(content, type=ModEntryModel, strict=True)
    except msgspec.ValidationError as e:
        raise ModEntryError(f'{prefix}invalid entry value: {e}') from None
    if not model.name:
        raise ModEntryError(
            f'{prefix}name is required, a declaration names the mod it declares')
    return model


class ModEntry:
    """
    The entry of a mod: where its declaration lives, and the declaration.

    The location is given to the constructor, the declaration is read from
    ``<root>/<path_config>/_index/mod_entry.yaml`` on the first access of
    :attr:`data`, the way ``YamlConfig.data`` reads ``deploy.yaml``. A caller
    that already holds a declaration passes it in: a test builds the values it
    needs, the pack server parses the blob of a commit.

    The fields of the declaration are answered here as cached properties in
    the shape the runtime uses, so a caller reads ``entry.gui_language`` and
    never has to know that the file spells it as a list: the values are the
    ordered sets (``dict`` of ``None``) and the tuples of the old entry, with
    the repeats dropped in the declared order.

    Attributes:
        root (str): Absolute root folder of the mod, '' for an entry read
            without a location
        path_config (str): Layout of the config folder, MOD_CONFIG or
            ALASIO_CONFIG
        file (str): <root>/<path_config>/_index/mod_entry.yaml
        data (ModEntryModel): The declaration of the mod
    """

    def __init__(self, root='', path_config=MOD_CONFIG, data=None):
        """
        Args:
            root (str): Root folder of the mod, a relative path is resolved
                against the working folder. Defaults to '', an entry used
                without a working tree
            path_config (str): Layout of the config folder. Defaults to
                MOD_CONFIG, the only layout a mod has; ModEntry.alasio names
                ALASIO_CONFIG, the layout of the library itself
            data (ModEntryModel): Parsed declaration to use instead of reading
                the file. Defaults to None, data reads the declaration

        Raises:
            ValueError: If path_config is neither MOD_CONFIG nor ALASIO_CONFIG
        """
        if path_config not in (MOD_CONFIG, ALASIO_CONFIG):
            raise ValueError(
                f'path_config must be {MOD_CONFIG!r} or {ALASIO_CONFIG!r}, '
                f'got {path_config!r}')
        # A relative root would resolve against the working folder at every
        # use, resolving it once here keeps the entry of a worker (which
        # chdirs to the mod root) and one of the backend equal
        self.root = abspath(root) if root else ''
        self.path_config = path_config
        self.file = joinnormpath(joinnormpath(self.root, path_config), MOD_ENTRY_SUBPATH)
        self._data = data

    def __str__(self):
        """
        ModEntry(root="E:/mod", path_config="module/config")
        """
        return f'{self.__class__.__name__}(root="{self.root}", path_config="{self.path_config}")'

    def __bool__(self):
        return True

    """
    The declaration
    """

    @cached_property
    def data(self):
        """
        The declaration of the mod, read on the first access and cached.

        Returns:
            ModEntryModel: The validated declaration

        Raises:
            FileNotFoundError: If the folder declares no entry
            ModEntryError: If the declaration cannot be read
        """
        if self._data is None:
            self._data = _parse(atomic_read_bytes(self.file), source_name=self.file)
        return self._data

    @cached_property
    def name(self):
        """
        Returns:
            str: Name of the mod
        """
        return self.data.name

    @cached_property
    def path_main(self):
        """
        Returns:
            str: Relative path from the mod root to the scheduler
        """
        return self.data.path_main

    @cached_property
    def path_assets(self):
        """
        Returns:
            str: Relative path from the mod root to the assets folder
        """
        return self.data.path_assets

    @cached_property
    def asset_ext(self):
        """
        Returns:
            dict[str, None]: Valid file extensions of the assets
        """
        return dict.fromkeys(self.data.asset_ext)

    @cached_property
    def asset_lang(self):
        """
        Returns:
            dict[str, None]: Valid languages of the assets, in the tried order
        """
        return dict.fromkeys(self.data.asset_lang)

    @cached_property
    def gui_language(self):
        """
        Returns:
            dict[str, None]: Languages of the GUI
        """
        return dict.fromkeys(self.data.gui_language)

    @cached_property
    def task_entry_folders(self):
        """
        Returns:
            dict[str, None]: Folders scanned for task entry functions
        """
        return dict.fromkeys(self.data.task_entry_folders)

    @cached_property
    def mirrors(self):
        """
        The update source of the mod, the mirror structure of its server.

        A declaration that cannot be parsed is reported and read as no source:
        a malformed update source makes the mod unmanaged, it must not stop the
        backend or the caller that only reads the entry.

        Returns:
            Mirrors | None: The update source, None when the mod declares none
                or when the declaration is malformed
        """
        return self._read_mirrors(self.data.mirrors, 'update source')

    @cached_property
    def pypi_mirrors(self):
        """
        The mirrors of the PyPI simple index the distributions come from.

        A declaration that cannot be parsed is reported and replaced by the
        default mirrors, the source of a mod never leaves it without an index.

        Returns:
            Mirrors | None: The mirror structure, None when neither the
                declaration nor the default can be read
        """
        return self._read_mirrors(self.data.pypi_mirrors, 'pypi mirrors')

    def _read_mirrors(self, value, label):
        """
        Parse a mirror declaration of the entry.

        Args:
            value (str | dict): The declared mirror input
            label (str): Name of the declaration, for the warning

        Returns:
            Mirrors | None: The mirror structure, None when the declaration is
                empty or cannot be parsed
        """
        if not value:
            return None
        # The import is local on purpose: the config layer is imported by
        # everything, it must not pull the deploy layer at import time
        from alasio.deploy.httpclient.probe_mirrors import Mirrors

        try:
            return Mirrors.from_input(value)
        except (ValueError, TypeError) as e:
            logger.warning(f'Invalid {label} of "{self.name}" in "{self.file}": {e}')
            return None

    @cached_property
    def update_provide_pypi_mirrors(self):
        """
        Returns:
            tuple[str, ...]: PyPI indexes the update packs of the mod provide
        """
        return tuple(dict.fromkeys(self.data.update_provide_pypi_mirrors))

    @cached_property
    def requirements_file(self):
        """
        Returns:
            str: Relative path from the mod root to the requirements file
        """
        return self.data.requirements_file

    @cached_property
    def update_pack_deps(self):
        """
        Returns:
            tuple[str, ...]: Distributions updated from the pack channel
        """
        return tuple(dict.fromkeys(self.data.update_pack_deps))

    @cached_property
    def scheduler_priority(self):
        """
        The task order of the scheduler, in the DSL of alasio.base.filter.

        The edges are stripped: the DSL ignores the whitespace around the
        order, so a declaration written as a block scalar and one written as a
        plain scalar are the same order.

        Returns:
            str: The declared order
        """
        return self.data.scheduler_priority.strip()

    def iter_asset_lang(self):
        """
        Iter the asset languages of the mod, the shared assets first.

        Yields:
            str: '' for the shared assets, then the declared languages
        """
        yield ''
        yield from self.asset_lang

    """
    The location
    """

    @classmethod
    def is_mod(cls, root):
        """
        Check whether a folder is the root of a mod.

        A mod is the folder holding ``module/config/_index/mod_entry.yaml``,
        the one marker of a mod: the generated index files are outputs of a
        mod, not what makes it one, so a mod is a mod before its config is
        generated.

        Args:
            root (str): Folder to check

        Returns:
            bool: True when the folder declares a mod entry
        """
        file = joinnormpath(joinnormpath(abspath(root), MOD_CONFIG), MOD_ENTRY_SUBPATH)
        try:
            os.stat(file)
        except OSError:
            # a missing folder, a missing file, and a path component that is
            # a file: none of them declares a mod
            return False
        return True

    @classmethod
    def create(cls, path, uppath=1):
        """
        Create the entry of a mod from a file or a folder of the mod.

        The caller states how many levels it lives below the root of the mod,
        so the root is computed, never searched: a walk up would answer a
        foreign root when the layout drifts (a folder of another mod above it,
        or the repository that holds the mod) and the mod would silently read
        a declaration that is not its own.

        Args:
            path (str): File or folder of the mod, usually __file__. A relative
                path is resolved against the working folder
            uppath (int): Levels from path up to the root of the mod, 1 for
                the folder holding path. Defaults to 1

        Returns:
            ModEntry: The entry of the mod

        Raises:
            ModEntryError: If the folder at that level declares no mod entry
        """
        root = PathStr.new(normpath(abspath(path))).uppath(uppath)
        if not cls.is_mod(root):
            raise ModEntryError(
                f'No mod entry at "{root}": "{path}" is {uppath} level(s) below the root of '
                f'a mod, expected {MOD_ENTRY_PATH}')
        return cls(root)

    @classmethod
    def from_source(cls, source, source_name=''):
        """
        Parse the declaration of a mod entry, without touching the filesystem.

        The pack server reads the file of a commit from the git blob and
        passes the text here: there is no working tree and no location, the
        entry carries the declaration only.

        Args:
            source (str | bytes): Content of ``module/config/_index/mod_entry.yaml``
            source_name (str): Name of the source used in the error messages.
                Defaults to ''

        Returns:
            ModEntry: The entry, root is ''

        Raises:
            ModEntryError: If the declaration cannot be read
        """
        return cls(data=_parse(source, source_name=source_name))

    @classmethod
    def defaults(cls):
        """
        The entry of a mod whose newest revision declares none.

        A pack server reads the declaration of a commit whose repo may not
        have the file yet, not even at the newest commit: the defaults are the
        layout the mod has always had, and the mod still gets its packs. The
        entry carries no location and no name, it only answers the defaults of
        the declaration.

        Returns:
            ModEntry: The entry of the defaults
        """
        return cls(data=ModEntryModel())

    @cached_class_property
    def alasio(cls):
        """
        The entry of the library itself, a singleton of the class.

        The library is not a mod: its declaration ships with the code rather
        than being read from the package at run time, so importing the config
        layer never touches the disk. ``alasio/config/_index/mod_entry.yaml``
        is written from this value by :meth:`write`, and a test keeps the two
        equal.

        Returns:
            ModEntry: The entry of the library
        """
        return cls(
            root=env.ALASIO_ROOT,
            path_config=ALASIO_CONFIG,
            data=ModEntryModel(
                name='alasio',
                gui_language=['zh-CN', 'en-US', 'ja-JP', 'zh-TW', 'en-ES'],
            ),
        )

    """
    The file
    """

    def write(self, gitadd=None):
        """
        Write the declaration of the mod, comments and format included.

        The file is written from the model, so its keys follow the model, its
        comments are the ``help`` annotations of the model and its format is
        the one of the yaml config: an edit of the model reaches every mod on
        the next write. Nothing is written when the file already holds the
        content.

        Args:
            gitadd (GitAdd | None): Stage the file when it changed. Defaults to
                None, the file is only written

        Returns:
            bool: if write
        """
        # The import is local on purpose: the yaml config pulls the code
        # generator layer, which the runtime import of an entry must not need
        from alasio.ext.file.yamlconfig import YamlConfig

        op = YamlConfig(self.file, ModEntryModel).write_data(self.data, skip_same=True)
        if not op:
            return False
        logger.info(f'Write file {self.file}')
        if gitadd:
            gitadd.stage_add(self.file)
        return True

    def exist(self):
        """
        Check whether the config of the mod is generated.

        The generated index is an output of a mod, not what makes it one, see
        :meth:`is_mod`: this check is about the mod being ready to run.

        Returns:
            bool:
        """
        # Mod must have config.index.json
        file = joinnormpath(self.root, self.path_config)
        file = joinnormpath(file, '_index/config.index.json')
        return os.path.exists(file)

    def copy(self):
        """
        Returns:
            ModEntry: A copy of the entry, the declaration is copied too
        """
        return deepcopy(self)

    @cached_property
    def pypi_record_name(self):
        """
        The name of this mod in the record of the pypi mirror probes
        (alasio.deploy.httpclient.probe_db): the mods whose pypi mirrors are
        the default share one record (''), whichever of them probes first
        serves the others, the mirrors are probed once per machine instead of
        once per mod; a mod with mirrors of its own keeps a record of its own
        (the mod name), its selection never overwrites the shared one.

        The compare is on the digest of the mirror structure (the group
        boundaries and the mirror names in their order; the urls and the
        group names are not part of it, see Mirrors.digest): two entries
        share the record exactly when the probe engine would reuse it for
        both. Cached: the mirrors of an entry are fixed at creation.

        Returns:
            str: '' when the mirrors are the default, the mod name otherwise
        """
        # The import is local on purpose: the config layer is imported by
        # everything, it must not pull the deploy layer at import time
        from alasio.deploy.httpclient.probe_mirrors import Mirrors

        mirrors = self.pypi_mirrors
        if mirrors is None:
            return self.name
        default = Mirrors.from_input(DEFAULT_PYPI_MIRRORS)
        if mirrors.digest == default.digest:
            return ''
        return self.name
