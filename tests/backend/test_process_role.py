"""
Tests of env.PROCESS_ROLE, the switch that tells a model loader how to load.

The role defaults to 'worker' so a plain `python -m module.main`, a script or a
test behaves like a real worker without setting anything. The backend marks
itself from its ASGI lifespan, so any server that runs the app -- the process
entry used by the supervisor or a manual one -- gets the right role.
"""

from alasio.ext import env


def test_default_is_worker():
    """Nothing sets the role, a fresh interpreter is a worker."""
    assert env.PROCESS_ROLE == 'worker'


def test_set_backend_role(monkeypatch):
    """The backend marks this process as the backend before serving."""
    from alasio.backend.app.app import set_backend_role

    monkeypatch.setattr(env, 'PROCESS_ROLE', 'worker')

    result = set_backend_role()

    assert env.PROCESS_ROLE == 'backend'
    # a plain function, not a decorated one that only prepares a generator
    assert result is None


def test_lifespan_is_an_async_context_manager():
    """Only the lifespan carries the asynccontextmanager decorator."""
    from alasio.backend.app import app as app_module

    # asynccontextmanager sets __wrapped__ on the function it returns
    assert hasattr(app_module.lifespan, '__wrapped__')
    assert not hasattr(app_module.set_backend_role, '__wrapped__')
    cm = app_module.lifespan(None)
    assert hasattr(cm, '__aenter__')


def test_lifespan_calls_set_backend_role():
    """The lifespan runs the role switch, a manual ASGI server included."""
    import inspect

    from alasio.backend.app import app as app_module

    source = inspect.getsource(app_module.lifespan)
    assert 'set_backend_role()' in source
