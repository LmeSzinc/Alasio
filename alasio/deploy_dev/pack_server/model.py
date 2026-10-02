from typing import List, Literal

from msgspec import Meta, Struct, field
from typing_extensions import Annotated

from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.ext import env
from alasio.ext.file.yamlconfig import YamlConfig
from alasio.ext.path.validate import validate_resolve_filepath
from alasio.logger import logger


class RepoConfig(Struct):
    """
    Clone repo to workspace/{Author}_{Repo}
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


class PackRepoModel(Struct):
    """
    1. Generate full packs to: pack/{Author}_{Repo}_{Branch}/{commit}/full_{commit}.pack
    only latest commit will have full pack
    2. Generate update packs to: pack/{Author}_{Repo}_{Branch}/{commit}/update_{old}.pack
    update packs will update from lookback commit to latest commit
    3. generate latest info to: pack/{Author}_{Repo}_{Branch}/latest.pack
    content is {new} version and the sha1 checksum of latest full pack
    4. folders that does not match the latest commit will be removed
    """
    Repo: RepoConfig = field(default_factory=RepoConfig)
    Lookback: LookbackConfig = field(default_factory=LookbackConfig)


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

    def __init__(self, file):
        check_run_dir()
        folder = env.PROJECT_ROOT.joinpath(self.CONFIG_FOLDER)
        file = validate_resolve_filepath(folder, file)
        super().__init__(file, model=PackRepoModel)

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
