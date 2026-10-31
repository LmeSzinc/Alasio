"""
Tests of env.PROCESS_ROLE, the switch that tells a model loader how to load.

The role defaults to 'worker' so a plain `python -m module.main`, a script or a
test behaves like a real worker without setting anything. Only the backend
process declares itself, from its own entry point.
"""

from alasio.ext import env


def test_default_is_worker():
    """Nothing sets the role, a fresh interpreter is a worker."""
    assert env.PROCESS_ROLE == 'worker'


def test_backend_entry_marks_the_process(monkeypatch):
    """The backend entry point flips the role before running the server."""
    import alasio.backend.app.asgi as asgi

    calls = []
    monkeypatch.setattr(asgi, 'run', lambda args: calls.append(args))
    monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')

    from alasio.backend.entry import backend_entry

    backend_entry(['--port', '8080'])

    assert env.PROCESS_ROLE == 'backend'
    assert calls == [['--port', '8080']]
