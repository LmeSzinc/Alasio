from alasio.ext.cache import cached_property
from alasio.ext.gitpython.fetch.argument import Arguments, Capabilities
from alasio.ext.gitpython.fetch.pkt import FetchPayload
from alasio.ext.gitpython.stage.gitcommit import GitCommit


class BaseTransport:
    """Abstract base class for all transport protocols.

    This class defines a high-level `fetch` interface that all concrete
    transport implementations must provide. It also offers protected helper
    methods to assist with common tasks like negotiation and stream handling.
    """

    def __init__(self, arguments: Arguments, capabilities: Capabilities = None):
        """Initializes the transport.

        Args:
            arguments: An Arguments object containing all fetch parameters.
            capabilities: A Capabilities object for advertising client features.
        """
        self.arguments = arguments
        if capabilities is None:
            capabilities = Capabilities()
        self.capabilities = capabilities

    @cached_property
    def repo(self):
        repo = GitCommit(self.arguments.repo_path)
        repo.read_lazy()
        return repo

    async def fetch(self, output_file) -> int:
        """Executes the full fetch process for the specific protocol.

        This method should handle everything from initial connection and
        service discovery to negotiation and streaming the packfile to the
        provided file object.

        Args:
            output_file: A file-like object opened in binary write mode ('wb')
                         to which the packfile data will be streamed.

        Returns:
            The total number of bytes of packfile data written to the file.
        """
        raise NotImplementedError

    def build_fetch_payload(self, want, depth=0, head=None):
        """
        Builds the complete negotiation request body in pkt-line format.

        Returns:
            FetchPayload:
        """
        payload = FetchPayload()
        # want <sha1> ...\n
        line = f'want {self.arguments.want_ref(want)} {self.capabilities.as_string()}'
        payload.add_line(line)

        if depth:
            payload.add_deepen(depth)

        # Delimiter between want/deepen and have list
        payload.add_delimiter()

        if head:
            have_commits = self.repo.list_commit_have(head, have_lookback=self.arguments.have_lookback)
            for have in have_commits:
                payload.add_have(have)

        payload.add_done()
        return payload
