import io
from collections import defaultdict

from alasio.ext.deep import deep_default, deep_exist, deep_pop, deep_set
from alasio.ext.path.atomic import atomic_read_text, atomic_write


class GitConfig:
    def __init__(self, file):
        """
        Args:
            file (str): filepath to .git/config
        """
        self.file = file
        # <section>:
        #   <key>:
        #       value
        self.config: "dict[str, dict[str, str]]" = {}
        # the original config after read
        # if config == config_origin, skip file write
        self.config_origin: "dict[str, dict[str, str]]" = {}

    @staticmethod
    def new_parser():
        """
        Returns:
            configparser.ConfigParser:
        """
        import configparser
        # No interpolation
        parser = configparser.ConfigParser(interpolation=None)
        # Preserve case of keys/options
        parser.optionxform = str
        return parser

    def read(self):
        """
        Read git config and parse into nested dict
        """
        # read
        try:
            data = atomic_read_text(self.file)
        except FileNotFoundError:
            # treat as empty config
            self.config = {}
            self.config_origin = {}
            return

        # parse
        import configparser
        config = defaultdict(dict)
        parser = self.new_parser()
        try:
            parser.read_string(data)
            for section in parser.sections():
                for key, value in parser.items(section):
                    config[section][key] = value
        except configparser.Error as e:
            # treat as empty config
            self.config = {}
            self.config_origin = {}
            return {}

        # result
        self.config = config
        self.config_origin = config
        return config

    def write(self):
        """
        Read git config and parse into nested dict
        """
        if self.config == self.config_origin:
            # config not changed, no need to write
            return

        # encode
        parser = self.new_parser()
        for section_name, section_items in self.config.items():
            parser[section_name] = {}  # Add section
            for key, value in section_items.items():
                # Ensure values are strings, as configparser expects them
                parser[section_name][key] = str(value)

        # dump
        buffer = io.StringIO()
        parser.write(buffer)
        data = buffer.getvalue()
        buffer.close()

        # write
        atomic_write(self.file, data)

    def __enter__(self):
        self.read()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.write()

    def set(self, section, key, value):
        """
        Set a config

        Args:
            section (str):
            key (str):
            value (Any):
        """
        if value is True:
            value = 'true'
        elif value is False:
            value = 'false'
        else:
            value = str(value)
        deep_set(self.config, [section, key], value)

    def pop(self, section, key, default=None):
        """
        Remove a config

        Args:
            section (str):
            key (str):
            default (str):

        Returns:
            Any: The value just popped
        """
        return deep_pop(self.config, [section, key], default)

    def exist(self, section, key):
        """
        Check if a config exists

        Args:
            section:
            key:

        Returns:
            bool:
        """
        return deep_exist(self.config, [section, key])

    def http_proxy(self, proxy=''):
        """
        Set http.proxy and https.proxy

        Args:
            proxy (str): Such as "http://127.0.0.1:7890"
                If empty, unset proxy
        """
        if proxy:
            self.set('http', 'proxy', proxy)
            self.set('https', 'proxy', proxy)
        else:
            self.pop('http', 'proxy')
            self.pop('https', 'proxy')

    def ssl_verify(self, verify=True):
        """
        Set http.sslVerify and https.proxy
        """
        if verify:
            self.set('http', 'sslVerify', True)
        else:
            self.set('http', 'sslVerify', False)

    def init_client(self):
        """
        Init git config as client deploy
        """
        # always auto crlf
        self.set('core', 'autocrlf', True)
        # max compression
        self.set('core', 'compression', 9)
        # git index version 4 for smaller index file
        self.set('index', 'version', 4)

    def pop_github_action_config(self):
        """
        If you pack out a repo in github action, the repo will have extraheader of github action,
        resulting in git pull failures in outside ot github action, so here we remove the section
        """
        deep_pop(self.config, [f'http "https://github.com/'])

    def remote_seturl(self, remote, url):
        """
        Set remote url, auto create remote if not exist

        Equivalent to:
        git remote set-url {remote} {url}
        git remote add {remote} {url}
        """
        section = f'remote "{remote}"'
        self.set(section, 'url', url)
        # set a default fetch
        fetch = f'+refs/heads/*:refs/remotes/{remote}/*'
        deep_default(self.config, [section, 'fetch'], fetch)

    def remote_rm(self, remote):
        """
        Remove a remote

        Equivalent to:
        git remote rm {remote}
        """
        section = f'remote "{remote}"'
        deep_pop(self.config, [section])

    def branch_set(self, remote, branch):
        """
        Args:
            remote (str):
            branch (str):
        """
        section = f'branch "{branch}"'
        deep_set(self.config, [section, 'remote'], remote)
        deep_set(self.config, [section, 'merge'], f'refs/heads/{branch}')

    def branch_pop(self, branch):
        """
        Args:
            branch (str):
        """
        section = f'branch "{branch}"'
        deep_pop(self.config, [section])
