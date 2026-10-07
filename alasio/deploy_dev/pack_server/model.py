from typing import List, Literal, Union

from msgspec import Meta, Struct, field
from typing_extensions import Annotated

from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.ext import env
from alasio.ext.file.yamlconfig import YamlConfig
from alasio.ext.path.validate import validate_resolve_filepath
from alasio.logger import logger


class RepoConfig(Struct):
    """
    Clone repo to repo/{Author}_{Repo}
    set git remote "origin" to {Remote}
    """
    Remote: Annotated[str, Meta(extra={"help": [
        "Remote url, example: 'https://github.com/LmeSzinc/AzurLaneAutoScript'",
    ]})] = ""
    Author: Annotated[str, Meta(extra={"help": [
        "Example: LmeSzinc",
    ]})] = ""
    Repo: Annotated[str, Meta(extra={"help": [
        "Example: AzurLaneAutoScript",
    ]})] = ""
    Branch: Annotated[str, Meta(extra={"help": [
        "Example: master",
    ]})] = "master"


class LookbackConfig(Struct):
    """
    Generate update packs from lookback commits to latest commit.
    The strictest restriction below will be followed
    """
    MaxCommitCount: Annotated[int, Meta(extra={"help": [
        "Lookback commits with maximum count, 0 for no limit, example: 0",
    ]})] = 0
    MaxCommitDay: Annotated[int, Meta(extra={"help": [
        "Lookback commits before X days at maximum, 0 for no limit, example: 90",
    ]})] = 90
    MaxTagCount: Annotated[int, Meta(extra={"help": [
        "Lookback tags with maximum count, 0 for no limit, example: 0",
    ]})] = 0
    MaxTagDay: Annotated[int, Meta(extra={"help": [
        "Lookback tags before X days at maximum, 0 for no limit, example: 365",
    ]})] = 365
    Parent: Annotated[Literal['parent-0', 'parent-all'], Meta(extra={"help": [
        "Rule of the parent lookup of the commits to lookback",
        "[parent-all] Walk every parent of every commit, the versions of a branch",
        "merged into the branch are lookback versions too, use it for a branch",
        "that receives the merges of the released branches, e.g. dev",
        "[parent-0] Walk the first parent of every commit only, the versions of the",
        "merged branches are not lookback versions, use it for a main branch that",
        "only receives the merges of the feature branches, e.g. master",
        "[In most cases] parent-all",
    ]})] = 'parent-all'
    LookbackBranch: Annotated[List[str], Meta(extra={"help": [
        "Branches to lookback, in addition to the branch of RepoConfig",
        "The head of RepoConfig.Branch is the latest version, the only latest one.",
        "The version of every branch listed here is an ordinary lookback version",
        "Their history is walked as a whole, so the versions of a branch merged",
        "into another one are lookback versions too",
        "Example: ['master', 'dev', 'bug_fix']",
    ]})] = field(default_factory=list)
    AdditionalCommit: Annotated[List[str], Meta(extra={"help": [
        "List of additional commits to lookback, ignoring lookback restrictions",
    ]})] = field(default_factory=list)


class PythonMirrorInfo(Struct):
    """
    Download source of the dependencies of one PythonDepsConfig.PypiMirror entry
    """
    Url: Annotated[str, Meta(extra={"help": [
        "Base url of a PyPI simple index (PEP 503)",
        "Defaults to the official PyPI, 'https://pypi.org/simple'",
        "Example: 'https://mirrors.aliyun.com/pypi/simple'",
    ]})] = 'https://pypi.org/simple'
    Deps: Annotated[List[str], Meta(extra={"help": [
        "Dependencies to download from this url, PEP 503 normalized names",
        "A dependency that no entry of PypiMirror lists is downloaded from",
        "the official PyPI (https://pypi.org/simple)",
        "Example: ['httpx2', 'starlette']",
    ]})] = field(default_factory=list)


class PythonDepsConfig(Struct):
    """
    Build packs of the python dependencies of the repo.

    The dependency files of RequirementFiles are sampled over the lookback
    window, the wheels of the versions the repo asked for are fetched from
    PypiMirror, and the packs of the names of PackUpdate are built. An empty
    group builds nothing, the packs of the project tree are the same as
    without it.
    """
    RequirementFiles: Annotated[List[str], Meta(extra={"help": [
        "Dependency files to sample, paths relative to the root of the repo",
        "A file named 'pyproject.toml' is parsed as TOML, any other file as requirements",
        "Example: ['requirements.txt']",
    ]})] = field(default_factory=list)
    PypiMirror: Annotated[Union[str, List[PythonMirrorInfo]], Meta(extra={"help": [
        "Where the wheels of the dependencies are downloaded from",
        "Defaults to the official PyPI, 'https://pypi.org/simple'",
        "[str] Download every dependency from this index, e.g. 'https://mirrors.aliyun.com/pypi/simple'",
        "[list] Download the dependencies of each entry from its own Url, e.g. a private index",
        "[list] A dependency that no entry lists is downloaded from the official PyPI",
    ]})] = 'https://pypi.org/simple'
    PackUpdate: Annotated[List[str], Meta(extra={"help": [
        "Dependencies to build packs of, PEP 503 normalized names",
        "A dependency that is not listed is not fetched and gets no pack",
        "Example: ['httpx2', 'starlette']",
    ]})] = field(default_factory=list)


class PackRepoModel(Struct):
    """
    1. Generate full packs to: pack/{Author}_{Repo}_{Branch}/packrepo/{commit}/full_{commit}.pack
    only latest commit will have full pack
    2. Generate update packs to: pack/{Author}_{Repo}_{Branch}/packrepo/{commit}/update_{old}.pack
    update packs will update from lookback commit to latest commit
    3. generate latest info to: pack/{Author}_{Repo}_{Branch}/packrepo/latest.pack
    content is {new} version and the sha1 checksum of latest full pack
    4. folders that does not match the latest commit will be removed
    5. PythonDeps is not empty: additionally build the packs of the python
    dependencies of the repo to pack/{Author}_{Repo}_{Branch}/packdep/{dep}/
    """
    Repo: RepoConfig = field(default_factory=RepoConfig)
    Lookback: LookbackConfig = field(default_factory=LookbackConfig)
    PythonDeps: PythonDepsConfig = field(default_factory=PythonDepsConfig)


class PackRepoConfig(YamlConfig):
    """
    Config of a repo to pack, read from the config folder of the run directory

    The config files live in ``{run directory}/config``, the run directory is
    ``env.PROJECT_ROOT`` and must be a directory of its own, see
    :func:`alasio.deploy_dev.pack_server.gate.check_run_dir`.

    The yaml file is validated with PackRepoModel, a value that fails the
    validation falls back to its default and is collected in ``errors``. A
    file that does not exist is created from the model with the help comments,
    an invalid file is written back with them, see YamlConfig.

    config/template.yaml is the template of a config file, written on the
    start of the pack server, see write_template. It is not a config itself
    and is not run by the server.

    Author, Repo, Remote and Branch must not be empty: every user of the
    config needs them, so the check lives here and the users do not check
    them again. A config with an empty one raises ValueError, the created or
    repaired file stays on the disk for the operator to fill.

    Args:
        file (str): Name of the yaml file in the config folder, e.g.
            'LmeSzinc_AzurLaneAutoScript.yaml'

    Usage:
        config = PackRepoConfig('LmeSzinc_AzurLaneAutoScript_master.yaml')
        config.data.Repo.Remote
        config.data.Lookback.MaxCommitDay
    """

    # folder of the config files, relative to the run directory
    CONFIG_FOLDER = 'config'

    # template of a config file, written on the start of the pack server
    TEMPLATE_FILE = 'template.yaml'

    def __init__(self, file):
        check_run_dir()
        folder = env.PROJECT_ROOT.joinpath(self.CONFIG_FOLDER)
        file = validate_resolve_filepath(folder, file)
        super().__init__(file, model=PackRepoModel)
        self._check_repo()

    @classmethod
    def write_template(cls):
        """
        Ensure config/template.yaml, the config of an empty repo, is up to date

        The template is built from PackRepoModel directly and written by
        YamlConfig, the file on the disk is not read: a missing or edited
        template is replaced with the generated text, an up to date one is
        left alone, see YamlConfig.write. The operator copies the template
        to a config file, e.g. LmeSzinc_AzurLaneAutoScript_master.yaml, and
        fills the values; the template itself is not a config and is not run
        by the pack server.

        Returns:
            bool: True if the file was written, False if it is up to date
        """
        file = env.PROJECT_ROOT.joinpath(cls.CONFIG_FOLDER).joinpath(cls.TEMPLATE_FILE)
        return YamlConfig(file, model=PackRepoModel).write(template=True)

    def _check_repo(self):
        """
        Check the fields of the repo that every user of the config needs

        An empty one cannot be worked around: the folder of the repo cannot
        be named, the remote cannot be cloned, the branch cannot be fetched.
        The yaml file is the only source of an empty value, so the check
        lives in the reader and the users of the config do not check again.

        Raises:
            ValueError: If Author, Repo, Remote or Branch is empty
        """
        repo = self.data.Repo
        if not repo.Author:
            raise ValueError(
                f'Empty Author in the pack config, cannot name the folder of the repo, file="{self.file}"')
        if not repo.Repo:
            raise ValueError(
                f'Empty Repo name in the pack config, cannot name the folder of the repo, file="{self.file}"')
        if not repo.Remote:
            raise ValueError(f'Empty Remote in the pack config, cannot clone the repo, file="{self.file}"')
        if not repo.Branch:
            raise ValueError(f'Empty Branch in the pack config, cannot know what to fetch, file="{self.file}"')

    def _log_errors(self, errors):
        """
        Log each error or exception as a warning

        Unlike YamlConfig the file is named, so the repo with the broken
        config can be found back among the configs of the other repos.

        Args:
            errors (list[ErrorInfo | Exception]): Errors to log
        """
        for error in errors:
            logger.warning(f'Invalid pack config value: {error}, file={self.file}')
