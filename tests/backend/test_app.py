"""
Tests for alasio.backend.app.create_config.

Focus: the hypercorn SSL wiring. hypercorn Config uses `keyfile` /
`certfile` field names (not uvicorn-style `ssl_keyfile` /
`ssl_certfile`); assigning the wrong names would silently create plain
instance attributes and leave the port plaintext.
"""

import pytest

from alasio.backend.app import create_config


class FakeBackend:
    def __init__(self, ssl):
        self.Host = ''
        self.Port = 0
        self.WebuiSSLKey = '/path/key.pem' if ssl else None
        self.WebuiSSLCert = '/path/cert.pem' if ssl else None


class FakeDeployData:
    def __init__(self, ssl):
        self.Backend = FakeBackend(ssl)
        # create_config reads `DeployConfig().config.data`
        self.data = self

    def show(self):
        # create_config logs the deploy config through `.config.show()`
        pass


class FakeDeployConfig:
    def __init__(self, ssl):
        self.config = FakeDeployData(ssl)


class TestCreateConfig:
    def test_ssl_sets_hypercorn_keyfile_certfile(self, monkeypatch):
        """SSL configured: hypercorn must be given keyfile/certfile."""
        monkeypatch.setattr(
            'alasio.backend.app.apply_hypercorn_exclusivity_patch', lambda: None)
        monkeypatch.setattr('alasio.ext.env.set_project_root', lambda root: None)
        monkeypatch.setattr(
            'alasio.backend.app.DeployConfig',
            lambda: FakeDeployConfig(ssl=True),
        )

        config = create_config([])

        # the field names hypercorn actually reads
        assert config.keyfile == '/path/key.pem'
        assert config.certfile == '/path/cert.pem'
        assert config.ssl_enabled

    def test_no_ssl_leaves_plaintext(self, monkeypatch):
        """No SSL configured: hypercorn stays plaintext."""
        monkeypatch.setattr(
            'alasio.backend.app.apply_hypercorn_exclusivity_patch', lambda: None)
        monkeypatch.setattr('alasio.ext.env.set_project_root', lambda root: None)
        monkeypatch.setattr(
            'alasio.backend.app.DeployConfig',
            lambda: FakeDeployConfig(ssl=False),
        )

        config = create_config([])

        assert config.keyfile is None
        assert config.certfile is None
        assert not config.ssl_enabled


class TestBindAnnounce:
    """
    After a successful bind the backend must announce command:started to
    the supervisor pipe, so recv_loop's startup window (and with it the
    stdin listener) starts without waiting out startup_timeout.
    """

    def test_create_sockets_announces_started(self, monkeypatch, free_port):
        import builtins
        import multiprocessing

        parent_conn, child_conn = multiprocessing.Pipe()
        monkeypatch.setattr(builtins, '__mpipe_conn__', child_conn, raising=False)
        monkeypatch.setattr('alasio.ext.env.set_project_root', lambda root: None)
        monkeypatch.setattr(
            'alasio.backend.app.DeployConfig',
            lambda: FakeDeployConfig(ssl=False),
        )

        config = create_config(['--host', '127.0.0.1', '--port', str(free_port)])
        sockets = config.create_sockets()
        try:
            assert parent_conn.poll(timeout=1)
            assert parent_conn.recv_bytes() == b'command:started'
        finally:
            for sock in sockets.secure_sockets + sockets.insecure_sockets:
                sock.close()
            parent_conn.close()
            child_conn.close()

    def test_bind_failure_does_not_announce(self, monkeypatch):
        """
        A bind failure (port in use) must raise without announcing: the
        startup window stays open, so the supervisor treats the crash as a
        startup failure instead of restart-looping.
        """
        import builtins
        import multiprocessing
        import socket
        import sys

        # Occupy a port so create_sockets' bind must fail. On Windows the
        # blocker needs SO_EXCLUSIVEADDRUSE: a plain bind could otherwise
        # succeed against it (the port preemption the exclusivity patch
        # exists to prevent).
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == 'win32':
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        blocker.bind(('127.0.0.1', 0))
        blocker.listen()
        port = blocker.getsockname()[1]

        parent_conn, child_conn = multiprocessing.Pipe()
        monkeypatch.setattr(builtins, '__mpipe_conn__', child_conn, raising=False)
        monkeypatch.setattr('alasio.ext.env.set_project_root', lambda root: None)
        monkeypatch.setattr(
            'alasio.backend.app.DeployConfig',
            lambda: FakeDeployConfig(ssl=False),
        )

        config = create_config(['--host', '127.0.0.1', '--port', str(port)])
        try:
            with pytest.raises(OSError):
                config.create_sockets()
            # no announce: the startup window must stay open on bind failure
            assert not parent_conn.poll(timeout=0.5)
        finally:
            blocker.close()
            parent_conn.close()
            child_conn.close()
