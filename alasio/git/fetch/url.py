from typing import Optional

from msgspec import Struct

from alasio.ext.backport import removesuffix


# from urllib.parse
def _splitnetloc(url, start=0):
    delim = len(url)  # position of end of domain part of url, default is end
    for c in '/?#':  # look for delimiters; the order is NOT important
        wdelim = url.find(c, start)  # find first of this delim
        if wdelim >= 0:  # if found
            delim = min(delim, wdelim)  # use earliest delim position
    return url[start:delim], url[delim:]  # return (domain, rest)


class GitUrl(Struct, frozen=True, kw_only=True):
    """
    A msgspec model representing a parsed Git URL.

    This structure holds the constituent parts of a Git URL in a normalized form.
    It is designed to be immutable (`frozen=True`).
    """
    scheme: str
    host: Optional[str] = None
    port: Optional[int] = None
    path: str

    @classmethod
    def from_string(cls, url: str) -> "GitUrl":
        """
        Parses any common Git URL format into a GitUrl object using only
        string partition methods.

        The parsing logic follows a specific order of precedence:
        1. Standard URI formats (https://, ssh://, git://).
        2. SCP-like SSH format (user@host:path).
        3. Local file paths (as a fallback).

        Args:
            url: The raw Git URL string to parse.

        Returns:
            An instance of the GitUrl class.
        """
        # --- Step 1: Try to parse standard URI formats (e.g., https://...) ---
        scheme, sep, rest = url.partition("://")
        if sep:  # This check is true if "://" was found
            # Clean query parameters and fragments from the URL part
            authority, path_part = _splitnetloc(rest)
            path_part, _, _ = path_part.partition('#')
            path_part, _, _ = path_part.partition('?')
            path = removesuffix(path_part, '.git')
            if not path.startswith('/'):
                path = f'/{path}'

            # # Separate host and port from authority, removing userinfo first
            _, _, authority = authority.rpartition('@')

            host = authority
            port = None

            # Prioritize IPv6 with port format: [host]:port
            if host.startswith('[') and ']:' in host:
                host_part, _, port_str = host.rpartition(']:')
                # Normalize IPv6 by removing brackets from the host field.
                host = host_part[1:]
                if port_str:
                    try:
                        port = int(port_str)
                    except ValueError:
                        raise ValueError(f'Port "{port_str}" is not int, from "{url}"') from None
            # Otherwise, check for regular host:port format
            else:
                host_part, sep, port_str = host.rpartition(':')
                # A valid port must be numeric, and the host part must not contain colons
                # to avoid misinterpreting a bare IPv6 address as having a port.
                if sep and ':' not in host_part:
                    # {host}:{port}
                    if port_str:
                        try:
                            port = int(port_str)
                        except ValueError:
                            raise ValueError(f'Port "{port_str}" is not int, from "{url}"') from None
                    host = host_part
                # Normalize IPv6 by removing brackets from the host field.
                if host.startswith('['):
                    host = host[1:]
                if host.endswith(']'):
                    host = host[:-1]

            return cls(scheme=scheme, host=host, port=port, path=path)

        # --- Step 2: Try to parse SCP-like SSH format (e.g., git@github.com:user/repo) ---
        # This format must look like 'user@host:path'
        head, sep, path_part = url.partition(':')
        if sep and '@' in head:  # A colon exists, and an '@' is before it
            _, _, host = head.rpartition('@')
            if host:  # Ensure there is a hostname after the '@'
                path = removesuffix(path_part, '.git')
                if not path.startswith('/'):
                    path = f'/{path}'
                return cls(scheme='ssh', host=host, port=None, path=path)

        # --- Step 3: Default to a local file path ---
        # If it's not a standard URI or an SCP-like string, treat it as a file path.
        # Note: Local paths are not normalized in the same way as network paths.
        # We preserve the original structure (e.g., relative paths, Windows paths).
        # path = _normalize_path(url, is_network_path=False)
        path = removesuffix(url, '.git')
        if not path:
            path = '.'
        return cls(scheme='file', host=None, port=None, path=path)

    def _build_uri(self, scheme, userinfo='', git_suffix=True):
        """A private helper to build standard URI formats."""
        if self.host is None:
            raise ValueError("Cannot generate a network URL from a local file path (host is None).")

        host_part = self.host
        # Wrap IPv6 addresses in brackets for URI correctness
        if ':' in host_part:
            host_part = f"[{host_part}]"

        port_part = f":{self.port}" if self.port else ""

        userinfo_part = f"{userinfo}@" if userinfo else ""

        # Normalize path: remove leading slash, then add it back to ensure single slash
        path_part = self.path.lstrip('/')
        if git_suffix:
            path_part += ".git"

        return f"{scheme}://{userinfo_part}{host_part}{port_part}/{path_part}"

    def to_http(self, https=True, git_suffix=True):
        """
        Reconstructs the URL into an HTTP(S) format.

        Returns:
            str: https://{host}:{port}/{path}.git
        """
        scheme = "https" if https else "http"
        return self._build_uri(scheme=scheme, userinfo='', git_suffix=git_suffix)

    def to_ssh(self, git_suffix=True):
        """
        Reconstructs the URL into the standard ssh:// URI format.

        Returns:
            str: ssh://git@{host}/{path}.git
        """
        return self._build_uri(scheme="ssh", userinfo="git", git_suffix=git_suffix)

    def to_git(self, git_suffix=True):
        """
        Reconstructs the URL into the native git:// protocol format.

        Returns:
            str: git://{host}:{port}/{path}.git
        """
        return self._build_uri(scheme="git", userinfo='', git_suffix=git_suffix)

    def to_git_scp(self, git_suffix=True):
        """
        Reconstructs the URL into the SCP-like format (e.g., git@host:path).
        Note: This format does not support port numbers. Any port in the model will be ignored.

        Returns:
            str: git@{host}:{path}.git
        """
        if self.host is None:
            raise ValueError("Cannot generate an SCP-like URL from a local file path (host is None).")

        # SCP-like path must not have a leading slash
        path_part = self.path.lstrip('/')
        if git_suffix:
            path_part += ".git"

        return f"git@{self.host}:{path_part}"
