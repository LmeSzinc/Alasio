"""
Tests for the port probe of the standalone updater.

The probe answers one question: can the backend bind this port again. The
tests bind real sockets of this machine, which is why the file is marked
parallel only in the sense of the project mark - each test owns an ephemeral
port nobody else can collide with.
"""
import socket

import pytest

from alasio.deploy import updater

pytestmark = [
    pytest.mark.parallel,
]


class bound_socket:
    """Bind (and optionally listen on) a port, close it on exit."""

    def __init__(self, host='127.0.0.1', listen=False):
        """
        Args:
            host (str): Address to bind, '' for every interface
            listen (bool): Also call listen() on the socket
        """
        self.host = host
        self.listen = listen
        self.sock = None

    def __enter__(self):
        """
        Bind the socket.

        Returns:
            "socket.socket": The bound socket, getsockname() gives host/port
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((self.host, 0))
        if self.listen:
            self.sock.listen(1)
        return self.sock

    def __exit__(self, *args):
        """
        Close the socket.

        Args:
            args (tuple): Exception info, ignored
        """
        self.sock.close()
        self.sock = None


def free_port(host='127.0.0.1'):
    """
    Ask the kernel for a free port and release it.

    Args:
        host (str): Address to bind

    Returns:
        "tuple[str, int]": Host and port
    """
    with bound_socket(host) as sock:
        address = sock.getsockname()[:2]
    return address


class TestPortAvailable:
    """port_available() against real sockets."""

    def test_free_port(self):
        """A port nobody listens on is reported available."""
        host, port = free_port()
        assert updater.port_available(host, port) is True

    def test_listening_port(self):
        """A listening socket holds the port."""
        with bound_socket(listen=True) as sock:
            host, port = sock.getsockname()[:2]
            assert updater.port_available(host, port) is False

    def test_bound_but_not_listening(self):
        """A socket that is only bound and never listens still holds the port."""
        with bound_socket(listen=False) as sock:
            host, port = sock.getsockname()[:2]
            assert updater.port_available(host, port) is False

    def test_every_interface(self):
        """The probe accepts the empty host, which is how the backend binds."""
        with bound_socket(host='', listen=True) as sock:
            port = sock.getsockname()[1]
            assert updater.port_available('', port) is False

    def test_probe_does_not_keep_the_port(self):
        """The probe socket is closed, so the port stays bindable afterwards."""
        host, port = free_port()
        assert updater.port_available(host, port) is True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
            sock.listen(1)

    def test_probe_is_repeatable(self):
        """The probe leaks neither the socket nor the port over repeated calls."""
        host, port = free_port()
        results = [updater.port_available(host, port) for _ in range(5)]
        assert results == [True] * 5
