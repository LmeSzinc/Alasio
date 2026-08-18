"""
msgspec model of ``config/deploy.yaml``, converted from the official
``config/deploy.template.yaml`` of AzurLaneAutoScript.

The model is validated by :class:`alasio.ext.file.yamlconfig.YamlConfig`,
which writes the ``Meta(extra={"help": ...})`` annotations back into the
yaml file as comments, matched by the full key path. ``help`` is a list
of lines, each line is written as one comment row above the key.

Field names and nesting follow the yaml structure exactly, so the model
can be default constructed and unknown fields are ignored for forward
compatibility::

    yaml = YamlConfig('config/deploy.yaml', DeployConfig)
    yaml.data.Deploy.Git.Repository
"""

from typing import Optional

from msgspec import Meta, Struct, field
from typing_extensions import Annotated

from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.singleton import Singleton


class RepoConfig(Struct):
    Repository: Annotated[str, Meta(extra={"help": [
        "URL of AzurLaneAutoScript repository",
        "[CN user] Use 'git://git.lyoko.io/AzurLaneAutoScript' for faster and more stable download",
        "[Other] Use 'https://github.com/LmeSzinc/AzurLaneAutoScript'",
    ]})] = "https://github.com/LmeSzinc/AzurLaneAutoScript"
    Branch: Annotated[str, Meta(extra={"help": [
        "Branch of Alas",
        "[Developer] Use 'dev', 'app', etc, to try new features",
        "[Other] Use 'master', the stable branch",
    ]})] = "master"
    GitExecutable: Annotated[str, Meta(extra={"help": [
        "Filepath of git executable `git.exe`",
        "[Easy installer] Use './toolkit/Git/mingw64/bin/git.exe'",
        "[Other] Use you own git",
    ]})] = "./toolkit/Git/mingw64/bin/git.exe"
    GitProxy: Annotated[Optional[str], Meta(extra={"help": [
        "Set git proxy",
        "[CN user] Use your local http proxy (http://127.0.0.1:{port}) or socks5 proxy (socks5://127.0.0.1:{port})",
        "[Other] Use null",
    ]})] = None
    SSLVerify: Annotated[bool, Meta(extra={"help": [
        "Set SSL Verify",
        "[In most cases] Use true",
        "[Other] Use false to when connected to an untrusted network",
    ]})] = True


class PythonConfig(Struct):
    PythonExecutable: Annotated[str, Meta(extra={"help": [
        "Filepath of python executable `python.exe`",
        "[Easy installer] Use './toolkit/python.exe'",
        "[Other] Use you own python, and its version should be 3.7.6 64bit",
    ]})] = "./toolkit/python.exe"
    PypiMirror: Annotated[Optional[str], Meta(extra={"help": [
        "URL of pypi mirror",
        "[CN user] Use 'https://mirrors.aliyun.com/pypi/simple' for faster and more stable download",
        "[Other] Use null",
    ]})] = None
    InstallDependencies: Annotated[bool, Meta(extra={"help": [
        "Install dependencies at startup",
        "[In most cases] Use true",
    ]})] = True
    RequirementsFile: Annotated[str, Meta(extra={"help": [
        "Path to requirements.txt",
        "[In most cases] Use 'requirements.txt'",
        "[In AidLux] Use './deploy/AidLux/{version}/requirements.txt', version is default to 0.92",
    ]})] = "requirements.txt"


class AdbConfig(Struct):
    AdbExecutable: Annotated[str, Meta(extra={"help": [
        "Filepath of ADB executable `adb.exe`",
        "[Easy installer] Use './toolkit/Lib/site-packages/adbutils/binaries/adb.exe'",
        "[Other] Use you own latest ADB, but not the ADB in your emulator",
    ]})] = "./toolkit/Lib/site-packages/adbutils/binaries/adb.exe"
    ReplaceAdb: Annotated[bool, Meta(extra={"help": [
        "Whether to replace ADB",
        "Chinese emulators (NoxPlayer, LDPlayer, MemuPlayer, MuMuPlayer) use their own ADB, instead of the latest.",
        "Different ADB servers will terminate each other at startup, resulting in disconnection.",
        "For compatibility, we have to replace them all.",
        "This will do:",
        "  1. Terminate current ADB server",
        "  2. Rename ADB from all emulators to *.bak and replace them by the AdbExecutable set above",
        "  3. Brute-force connect to all available emulator instances",
        "[In most cases] Use true",
        "[In few cases] Use false, if you have other programs using ADB.",
    ]})] = True
    AutoConnect: Annotated[bool, Meta(extra={"help": [
        "Brute-force connect to all available emulator instances",
        "[In most cases] Use true",
    ]})] = True
    InstallUiautomator2: Annotated[bool, Meta(extra={"help": [
        "Re-install uiautomator2",
        "[In most cases] Use true",
    ]})] = True


class OcrConfig(Struct):
    UseOcrServer: Annotated[bool, Meta(extra={"help": [
        "Run Ocr as a service, can reduce memory usage by not import mxnet everytime you start an alas instance",
        "",
        "Whether to use ocr server",
        "[Default] false",
    ]})] = False
    StartOcrServer: Annotated[bool, Meta(extra={"help": [
        "Whether to start ocr server when start GUI",
        "[Default] false",
    ]})] = False
    OcrServerPort: Annotated[int, Meta(extra={"help": [
        "Port of ocr server runs by GUI",
        "[Default] 22268",
    ]})] = 22268
    OcrClientAddress: Annotated[str, Meta(extra={"help": [
        "Address of ocr server for alas instance to connect",
        "[Default] 127.0.0.1:22268",
    ]})] = "127.0.0.1:22268"


class UpdateConfig(Struct):
    AutoUpdate: Annotated[bool, Meta(extra={"help": [
        "Update Alas at startup",
        "[In most cases] Use true",
    ]})] = True
    CheckUpdateInterval: Annotated[int, Meta(extra={"help": [
        "Check update every X minute",
        "[Disable] 0",
        "[Default] 5",
    ]})] = 5
    AutoRestartTime: Annotated[str, Meta(extra={"help": [
        "Scheduled restart time",
        "If there are updates, Alas will automatically restart and update at this time every day",
        "and run all alas instances that running before restarted",
        "[Disable] null",
        "[Default] 03:50",
    ]})] = "03:50"


class MiscConfig(Struct):
    DiscordRichPresence: Annotated[bool, Meta(extra={"help": [
        "Enable discord rich presence",
    ]})] = False


class RemoteAccessConfig(Struct):
    EnableRemoteAccess: Annotated[bool, Meta(extra={"help": [
        "Enable remote access (using ssh reverse tunnel serve by https://github.com/wang0618/localshare)",
        "! You must set Password below to enable remote access since everyone can access to your alas if they have your url.",
        "See here (http://app.azurlane.cloud/en.html) for more infomation.",
    ]})] = False
    SSHUser: Annotated[Optional[str], Meta(extra={"help": [
        "Username when login into ssh server",
        "[Default] null (will generate a random one when startup)",
    ]})] = None
    SSHServer: Annotated[Optional[str], Meta(extra={"help": [
        "Server to connect",
        "[Default] null",
        "[Format] host:port",
    ]})] = None
    SSHExecutable: Annotated[str, Meta(extra={"help": [
        "Filepath of SSH executable `ssh.exe`",
        "[Default] ssh (find ssh in system PATH)",
        "If you don't have one, install OpenSSH or download it here "
        "(https://github.com/PowerShell/Win32-OpenSSH/releases)",
    ]})] = "ssh"


class BackendConfig(Struct):
    Host: Annotated[str, Meta(extra={"help": [
        "--host. Host to listen",
        "[Use IPv6] '::'",
        "[In most cases] Default to '0.0.0.0'",
    ]})] = "0.0.0.0"
    Port: Annotated[int, Meta(extra={"help": [
        "--port. Port to listen",
        "You will be able to access webui via `http://{host}:{port}`",
        "[In most cases] Default to 22267",
    ]})] = 22267
    # frontend language and theme is store on client, not backend
    Password: Annotated[Optional[str], Meta(extra={"help": [
        "--key. Password of web ui",
        "Useful when expose Alas to the public network",
    ]})] = None
    WebuiSSLKey: Annotated[Optional[str], Meta(extra={"help": [
        "SSL support",
        "Only effective when both parameters below are set",
        "--ssl-key. Path to SSL key file",
        "[Default] null (no SSL)",
    ]})] = None
    WebuiSSLCert: Annotated[Optional[str], Meta(extra={"help": [
        "--ssl-cert. Path to SSL cert file",
        "[Default] null (no SSL)",
    ]})] = None


class WebappConfig(Struct):
    Lang: Annotated[str, Meta(extra={"help": [
        "Language to use on webapp",
        "'system' to use system language",
        "'zh-CN' for Chinese simplified",
        "'en-US' for English",
        "'ja-JP' for Japanese",
        "'zh-TW' for Chinese traditional",
    ]})] = "system"
    Theme: Annotated[str, Meta(extra={"help": [
        "Theme of web ui",
        "'system' to follow system theme",
        "'light' for light theme",
        "'dark' for dark theme",
    ]})] = "system"
    DpiScaling: Annotated[bool, Meta(extra={"help": [
        "Follow system DPI scaling",
        "[In most cases] true",
        "[In few cases] false to make webapp window smaller, if you have a low resolution but high DPI scaling.",
    ]})] = True


class DeployModel(Struct):
    """
    Deploy config, section ``Deploy`` in deploy.yaml
    """
    Repo: RepoConfig = field(default_factory=RepoConfig)
    Python: PythonConfig = field(default_factory=PythonConfig)
    Adb: AdbConfig = field(default_factory=AdbConfig)
    Ocr: OcrConfig = field(default_factory=OcrConfig)
    Update: UpdateConfig = field(default_factory=UpdateConfig)
    Misc: MiscConfig = field(default_factory=MiscConfig)
    RemoteAccess: RemoteAccessConfig = field(default_factory=RemoteAccessConfig)
    Backend: BackendConfig = field(default_factory=BackendConfig)
    Webapp: WebappConfig = field(default_factory=WebappConfig)


class DeployConfig(metaclass=Singleton):
    @cached_property
    def config(self):
        from alasio.ext.file.yamlconfig import YamlConfig
        file = env.PROJECT_ROOT.joinpath('config/deploy.yaml')
        config = YamlConfig(file, model=DeployModel)
        if config.errors:
            config.write()
        return config
