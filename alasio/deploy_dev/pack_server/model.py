from typing import Dict, List, Literal, Union

from msgspec import Meta, Struct, field
from typing_extensions import Annotated

from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.ext import env
from alasio.ext.file.yamlconfig import YamlConfig
from alasio.ext.path.validate import validate_filename, validate_resolve_filepath
from alasio.logger import logger


class ConfigError(Exception):
    """
    Raised when a config file, a credential or a remote setting is not usable.

    Every check of the configuration raises this one error, so a caller tells
    "a value the operator has to edit" from every other failure by its type:
    an unknown key, an empty required field, a malformed proxy, a remote the
    driver cannot serve, a credential the environment does not hold, a bucket
    that does not exist. A run stops at the first one instead of retrying,
    because another attempt cannot change a value of a file.

    The message names the field and the file, never a credential.
    """


def require_valid(config):
    """
    Refuse a config file that the reader could not validate.

    YamlConfig falls back to the default of a field that fails the validation,
    which is what the generator can live with (a broken Lookback value still
    generates the packs it can) but not what a run of the config can: a typo in
    Publish would drop a remote, publish nowhere, and report success. The errors
    of the file are read here, so the run refuses it.

    Args:
        config (YamlConfig): The config of a repo, its data is read here

    Raises:
        ConfigError: If any value of the file failed the validation
    """
    # the errors are filled by the read of the data, see YamlConfig.data
    _ = config.data
    errors = [error for error in config.errors if not isinstance(error, FileNotFoundError)]
    if errors:
        reasons = '; '.join(str(error) for error in errors)
        raise ConfigError(f'Cannot read "{config.file}": {reasons}')


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
    StopCommit: Annotated[str, Meta(extra={"help": [
        "Oldest commit to lookback, '' for no limit, example: '6a1f2e0', or full commit sha1",
        "Every commit older than it is not a lookback version, on any branch. The commit itself is kept.",
        "It applies to the commits of the branches and of the tags alike,",
        "so a tag pointing at an older commit is dropped too.",
    ]})] = ""


class EnvConfig(Struct):
    """
    The environment of the machine the pack server runs on
    """
    Proxy: Annotated[str, Meta(extra={"help": [
        "Proxy of the index pages and wheels the server downloads, '' for a direct connection",
        "Example: 'http://127.0.0.1:7890', 'socks5://127.0.0.1:7890'",
        "The wheels are downloaded from the index the pin declares (PyPI, py38deps, ...),",
        "never from the mirror a client installs from: the mirror is generated by the pack",
        "server itself, so it cannot be the source of the server (chicken and egg).",
        "Set the proxy when the indexes are not reachable from this machine.",
    ]})] = ""


class RemoteBase(Struct, forbid_unknown_fields=True):
    """
    The publish settings every remote has, whatever its driver
    """
    Concurrency: Annotated[int, Meta(extra={"help": [
        "Concurrent transfers in flight to this remote, 1 to 8",
        "Past 8 the queue only moves into the connection pool, the uplink is the limit",
    ]})] = 4
    GraceSeconds: Annotated[int, Meta(extra={"help": [
        "Retire grace of this remote in seconds",
        "The cleaner deletes a retired key only after the barrier of THIS remote",
        "has been in place for this long, and only if the key is older as well",
        "Must cover the CDN TTL plus one full client update",
    ]})] = 3600
    EnvPrefix: Annotated[str, Meta(extra={"help": [
        "Environment variable prefix of the credentials of this remote",
        "'' for the default prefix of the driver: ALASIO_COS for 'cos'",
        "'ALASIO_COS_BACKUP' -> ALASIO_COS_BACKUP_SECRET_ID / _SECRET_KEY",
    ]})] = ""
    TimeoutSeconds: Annotated[int, Meta(extra={"help": [
        "Read/write timeout of one request",
        "A 100 MB pack on a 10 Mbit/s uplink takes about 80 s",
    ]})] = 120


class CosRemote(RemoteBase, tag='cos', tag_field='Driver'):
    """
    A Tencent COS bucket; the Driver key of the file reads 'cos'
    """
    Bucket: Annotated[str, Meta(extra={"help": [
        "COS bucket name with the APPID suffix, e.g. 'alasio-1250000000'",
    ]})] = ""
    Region: Annotated[str, Meta(extra={"help": [
        "COS region, e.g. 'ap-guangzhou'",
    ]})] = ""
    Endpoint: Annotated[str, Meta(extra={"help": [
        "Custom origin endpoint, '' for https://{Bucket}.cos.{Region}.myqcloud.com",
        "A host only, never a CDN domain: the cleaner must read the origin",
    ]})] = ""
    Prefix: Annotated[str, Meta(extra={"help": [
        "Key prefix inside the bucket, '' for no prefix",
        "The object key is {Prefix}/{workspace}/{relative path}",
    ]})] = ""


class WebDavRemote(RemoteBase, tag='webdav', tag_field='Driver'):
    """
    A WebDAV collection; the Driver key of the file reads 'webdav'

    The driver is not implemented yet: the entry is accepted by the model so a
    config that mentions one is not rewritten with the entry dropped, and the
    store factory refuses it with a message that says so.
    """
    BaseUrl: Annotated[str, Meta(extra={"help": [
        "URL of the collection that holds the workspaces, no trailing slash",
        "The object URL is {BaseUrl}/{workspace}/{relative path}",
        "The origin, never a CDN domain: the cleaner must read the origin",
        "Example: 'https://nas.example:5006/dav/pack'",
    ]})] = ""
    VerifyTls: Annotated[bool, Meta(extra={"help": [
        "Verify the TLS certificate of the server",
        "false only for a self-signed host in a network you trust",
    ]})] = True


# the discriminant is the Driver key: msgspec picks the variant from it and
# writes it back, so the file keeps saying which driver a remote is
RemoteConfig = Union[CosRemote, WebDavRemote]


class PackRepoModel(Struct):
    """
    1. Workspace is always `pack/{Author}_{Repo}_{Branch}`
    2. Generate full packs and update packs for repo
      - [2.1] Generate full packs to: {workspace}/packrepo/{commit}/full.pack
        only latest commit will have full pack
      - [2.2] Generate update packs to: {workspace}/packrepo/{commit}/update_{old}.pack
        update packs will update from lookback commit to latest commit
    3. Download pypi dep index page https://.../{dep_name}/
      to {workspace}/.wheel_cache/{dep_name}/{dep_version}/index.html
    4. Download wheels to: {workspace}/.wheel_cache/{dep_name}/{dep_version}/*.whl
      If wheels already exists, validate sha256 from {dep_name}/{dep_version}/index.html
      {dep_name}/{dep_version} comes from:
      - [4.1] deps that declared in `ModEntry.requirements_file` and its name hits `ModEntry.pack_deps`
        from both latest commit and lookback commits
      - [4.2] deps that declared in `ModEntry.requirements_file` and its source hits
        `ModEntry.provide_wheel`
        from latest commit only
    5. Sync wheels from [4.2] to {workspace}/wheel/{dep_name}/{dep_version}/*.whl
    6. Generate full packs and update packs for deps that declared in `ModEntry.provide_wheel`
      - [6.1] Generate full packs to: {workspace}/packdep/{dep_name}/{dep_version}/full.pack
        only the deps defined in latest commit will have full pack
      - [6.2] Generate update packs to: {workspace}/packdep/{dep_name}/{dep_version}/update_{old}.pack
        update packs will update from old version to latest version
    7. Generate latest info to: {workspace}/packrepo/latest.pack
      content is {new} version and the sha1 checksum of latest full pack
      Replacing `latest.pack` indicates repo version switch

    The index pages and the wheels of the steps above are downloaded from the
    index a pin declares, never from a mirror of the client: the mirror is the
    output of the server, so the server downloading from it would be a chicken
    and egg problem. The machine of the server reaches the indexes itself, or
    through `Env.Proxy` when it cannot.
    """
    Repo: RepoConfig = field(default_factory=RepoConfig)
    Lookback: LookbackConfig = field(default_factory=LookbackConfig)
    Env: EnvConfig = field(default_factory=EnvConfig)
    Publish: Annotated[Dict[str, RemoteConfig], Meta(extra={"help": [
        "Static hosts to publish this repo to, keyed by a name of your choice",
        "Each key creates one independent mirror with its own settings",
        "An empty map publishes nowhere, the run of the repo only generates packs",
        "Driver picks the fields the remote has: 'cos' or 'webdav'",
        "Credentials are read from the environment, never written here",
        "Example:",
        "  Publish:",
        "    cos-main:",
        "      Driver: 'cos'",
        "      Bucket: 'alasio-1250000000'",
        "      Region: 'ap-guangzhou'",
        "      Prefix: 'pack'",
        "      EnvPrefix: 'ALASIO_COS'",
    ]})] = field(default_factory=dict)

    @property
    def workspace_name(self):
        """
        Name of the workspace of this repo: {Author}_{Repo}_{Branch}

        Every step of the flow writes under ``pack/{workspace_name}``, and the
        publication mirrors that folder, so the name must be a single path
        component: a Branch like 'feature/x' would nest the folder of this repo
        inside the folder of another config, and the cleanup of the stale
        folders of that config could remove the packs of this one.

        Returns:
            str: E.g. 'LmeSzinc_AzurLaneAutoScript_master'

        Raises:
            ConfigError: If the name is not a single safe path component
        """
        name = f'{self.Repo.Author}_{self.Repo.Repo}_{self.Repo.Branch}'
        try:
            validate_filename(name)
        except ValueError as e:
            raise ConfigError(f'Invalid workspace name "{name}": {e}') from None
        return name


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
    them again. A config with an empty one raises ConfigError, the created or
    repaired file stays on the disk for the operator to fill. The proxy of the
    Env group is checked the same way: a value the http client cannot use would
    fail every download of the run, long after the clone. The values of the
    Publish group that cannot be worked around, a negative grace or a timeout
    of zero, are refused as well.

    Args:
        file (str): Name of the yaml file in the config folder, e.g.
            'LmeSzinc_AzurLaneAutoScript.yaml'

    Usage:
        config = PackRepoConfig('LmeSzinc_AzurLaneAutoScript_master.yaml')
        config.data.Repo.Remote
        config.data.Lookback.MaxCommitDay
        config.data.Publish['cos-main']
    """

    # folder of the config files, relative to the run directory
    CONFIG_FOLDER = 'config'

    # template of a config file, written on the start of the pack server
    TEMPLATE_FILE = 'template.yaml'

    def __init__(self, file):
        check_run_dir()
        folder = env.PROJECT_ROOT.joinpath(self.CONFIG_FOLDER)
        try:
            file = validate_resolve_filepath(folder, file)
        except ValueError as e:
            raise ConfigError(f'Invalid config file name: {e}') from None
        super().__init__(file, model=PackRepoModel)
        self._check_repo()
        self._check_env()
        self._check_publish()

    @classmethod
    def iter_files(cls):
        """
        Iter the config files of the run directory

        The config files are the *.yaml files of the config folder, sorted by
        name so a run follows the same order every time. The template is
        skipped, it is the file the operator copies and fills, and a file that
        is not a *.yaml file is not a config either, e.g. the tmp file of an
        interrupted write (file.yaml.xxxxxxxx.tmp). A folder that does not
        exist has no config.

        Yields:
            str: File name of a config in the config folder, e.g.
                'LmeSzinc_AzurLaneAutoScript_master.yaml'
        """
        folder = env.PROJECT_ROOT.joinpath(cls.CONFIG_FOLDER)
        for name in sorted(folder.iter_filenames(ext='.yaml')):
            if name != cls.TEMPLATE_FILE:
                yield name

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

    def _invalid_values(self):
        """
        The values of the file the reader refused, for the message of a check

        YamlConfig falls back to the default of a value that fails the
        validation, and a value of a struct resets the whole model: the empty
        field checks below would then report a missing Author while the real
        cause is a typo of the file. The causes are appended to that message.

        Returns:
            str: ', the file holds invalid values: ...', '' when the file has none
        """
        reasons = [str(error) for error in self.errors if not isinstance(error, FileNotFoundError)]
        if not reasons:
            return ''
        return ', the file holds invalid values: ' + '; '.join(reasons)

    def _check_repo(self):
        """
        Check the fields of the repo that every user of the config needs

        An empty one cannot be worked around: the folder of the repo cannot
        be named, the remote cannot be cloned, the branch cannot be fetched.
        The yaml file is the only source of an empty value, so the check
        lives in the reader and the users of the config do not check again.

        Raises:
            ConfigError: If Author, Repo, Remote or Branch is empty
        """
        repo = self.data.Repo
        if not repo.Author:
            raise ConfigError(
                f'Empty Author in the pack config, cannot name the folder of the repo, '
                f'file="{self.file}"{self._invalid_values()}')
        if not repo.Repo:
            raise ConfigError(
                f'Empty Repo name in the pack config, cannot name the folder of the repo, '
                f'file="{self.file}"{self._invalid_values()}')
        if not repo.Remote:
            raise ConfigError(
                f'Empty Remote in the pack config, cannot clone the repo, '
                f'file="{self.file}"{self._invalid_values()}')
        if not repo.Branch:
            raise ConfigError(
                f'Empty Branch in the pack config, cannot know what to fetch, '
                f'file="{self.file}"{self._invalid_values()}')

    def _check_env(self):
        """
        Check the Env group: the proxy of the downloads must be usable

        The downloads are the only way the wheels reach the workspace, and the
        proxy is the only way a blocked machine reaches the indexes, so a value
        the http client cannot use fails every fetch of the run: the run is
        failed here, before the clone and the lookback, instead of failing
        every request of the first step that fetches.

        Raises:
            ConfigError: If Env.Proxy is not a proxy url the http client can use
        """
        proxy = self.data.Env.Proxy
        if not proxy:
            return
        # httpx2 is imported lazily: the package resolves its own version with
        # importlib.metadata at import time
        import httpx2
        try:
            url = httpx2.Proxy(proxy).url
        except ValueError as e:
            raise ConfigError(
                f'Invalid Env.Proxy of the pack config, expected a proxy url like '
                f'"http://127.0.0.1:7890": {e}, file="{self.file}"') from None
        if not url.host:
            raise ConfigError(
                f'Invalid Env.Proxy of the pack config, the host is empty: {proxy!r}, '
                f'file="{self.file}"')

    def _check_publish(self):
        """
        Check the Publish group: the remotes must be usable

        A negative grace would retire a generation before the clients saw it,
        and a timeout of zero fails every request of a remote, so both are
        refused here instead of failing the upload of the repo later. The
        fields a driver needs (Bucket, Region, BaseUrl) are checked by the
        driver itself, before the first request: the model does not know which
        driver serves which variant. Concurrency is not checked here: the
        flows clamp it to 1..8, a value outside is a preference, not an error.

        Raises:
            ConfigError: If GraceSeconds is negative, or TimeoutSeconds is not
                positive
        """
        for name, remote in self.data.Publish.items():
            if remote.GraceSeconds < 0:
                raise ConfigError(
                    f'Publish.{name}.GraceSeconds must be >= 0, file="{self.file}"')
            if remote.TimeoutSeconds <= 0:
                raise ConfigError(
                    f'Publish.{name}.TimeoutSeconds must be > 0, file="{self.file}"')

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
