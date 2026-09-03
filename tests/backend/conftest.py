"""
Fixtures for the backend tests.
"""

import socket

import pytest


@pytest.fixture
def free_port():
    """
    Provide a free localhost port for tests that bind a listener.

    The port is reserved by binding to 127.0.0.1:0 (the OS assigns an
    unused port) and released before the test binds it again, so tests
    never collide with a running backend or with each other.

    Note: create_config treats `--port 0` as "not given" (falsy) and
    falls back to the configured port (8000 by default), so tests of the
    backend must pass the returned port explicitly.

    Returns:
        int: An unused port on 127.0.0.1
    """
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
