from alasio.ext.cache import cached_property
from alasio.ext.gitpython.fetch.url import GitUrl
from alasio.ext.path import PathStr


class Arguments:
    """
    Encapsulates arguments for a fetch or clone operation.

    This class uses class attributes for default values, allowing for easy
    instantiation with minimal configuration for common cases.
    """
    # Absolute filepath to repo, that contains a .git directory
    repo_path: PathStr
    # URL to repo, HTTP or GIT protocol, note that SSH is not supported yet
    # e.g. https://github.com/LmeSzinc/AzurLaneAutoScript
    # e.g. git://git.lyoko.io/AzurLaneAutoScript
    repo_url: GitUrl
    # Optional HTTP proxy, socks proxy not supported yet
    # e.g. http://127.0.0.1:7890
    proxy: str = None
    # 0 for no depth set
    # 1 for "--depth 1"
    depth: int = 0
    # Query for a commit, or branch
    # Can be one of:
    # - commit sha1 like "d5f854cedd666de9a99ebacbd3fb47971afc4271"
    # - branch name like "master", which will later convert to "refs/heads/{want}"
    # - any git ref like "refs/pull/1007/head", "refs/tags/v2020.04.08"
    want: str = 'master'
    # Send N `have`s when wanting a commit, 20 is an arbitrary number.
    # If local is <20 commits ahead of remove, update can be done in one negotiation.
    # If have_lookback is 0, send the entire have list letting server decide.
    have_lookback = 20
    # 256KB default write buffer size
    buffer_size: int = 262144

    def __init__(self, repo_path, repo_url):
        """
        Args:
            repo_path (str):
            repo_url (str):
        """
        self.repo_path = PathStr.new(repo_path)
        if isinstance(repo_url, str):
            repo_url = GitUrl.from_string(repo_url)
        self.repo_url = repo_url

    @cached_property
    def path_pack(self) -> str:
        """
        Note that you shouldn't write into this file directly.
        Write to temp file first, then do atomic replace.

        Returns:
            PathStr:
        """
        import secrets
        random_name = secrets.token_hex(16)
        return self.repo_path / f'.git/objects/pack/pack-{random_name}.pack'

    def want_ref(self, want=None):
        """
        Args:
            want (str): Query for a commit, or branch

        Returns:
            str:
        """
        if want is None:
            want = self.want
        # consider any length=40 as commit sha1
        if len(want) == 40:
            return want
        # use refs/* directly
        if want.startswith('refs/'):
            return want
        # otherwise treat as branch name
        return f'refs/heads/{want}'


class Capabilities:
    """
    Encapsulates client capabilities to be advertised to the server.

    This class holds boolean flags for various Git protocol capabilities.
    An instance of this class can be converted to a space-separated
    string suitable for the protocol.
    """
    # Enables detailed multi_ack responses
    multi_ack_detailed: bool = True
    # Signals the server that the client will send 'done' at the end of the negotiation.
    no_done: bool = True
    # Enables the 64k side-band multiplexing for progress reporting and packfile data
    side_band_64k: bool = True
    # Enables thin packs, where the server can send objects that reference objects the client already has
    thin_pack: bool = True
    # Indicates support for offset-based deltas in packfiles
    ofs_delta: bool = True
    # A string identifying the client agent
    # Pretend we are a git client
    agent: str = 'git/2.28.0'
    # Use Git protocol V2
    protocol_v2: bool = True

    def as_string(self) -> str:
        """Converts the enabled capabilities to a protocol string.

        Returns:
            A space-separated string of enabled capability names.
        """
        caps = []
        if self.multi_ack_detailed:
            caps.append('multi_ack_detailed')
        if self.no_done:
            caps.append('no-done')
        if self.side_band_64k:
            caps.append('side-band-64k')
        if self.thin_pack:
            caps.append('thin-pack')
        if self.ofs_delta:
            caps.append('ofs-delta')
        if self.agent:
            caps.append(f'agent={self.agent}')

        return ' '.join(caps)

    def headers(self, protocol_v2=None):
        """
        Get http headers

        Args:
            protocol_v2 (bool): override protocol_v2

        Returns:
            dict[str, str]:
        """
        out = {
            'User-Agent': self.agent,
        }
        if protocol_v2 is None:
            protocol_v2 = self.protocol_v2
        if protocol_v2:
            out['Git-Protocol'] = 'version=2'
        return out
