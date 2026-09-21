"""
Process import isolation tests.

Supervisor / backend / worker are three process layers started with
multiprocessing spawn. Each layer must not import the exclusive content of
the other layers at startup:

- supervisor: no backend web stack (starlette / hypercorn / trio), no worker
  content (msgspec), no logger
- backend: no supervisor-only modules (supervisor.py / backend.py /
  token_supervisor) -- the spawn target and the backend entry callable live
  in alasio.backend.entry, a stdlib-only module
- worker: no backend web stack, no supervisor-only modules

Each test runs in an isolated spawn subprocess (HeavyImportTest) so the
module set is measured from a clean interpreter, and the simulated startup
imports mirror the real spawn sequence (gui.py module level re-import +
pickle target / args deserialization + entry body).
"""
import importlib.util

from alasio.ext import env
from tests.backend.import_testing import HeavyImportTest

# gui.py sits at the project root
GUI_PATH = env.ALASIO_ROOT.joinpath('gui.py')

# Blacklists per process: modules a process must never import at startup.
#
# logger is used by both backend and worker, so it only appears in the
# supervisor blacklist. msgspec is worker's event codec (shared with
# backend), so it appears in the supervisor blacklist only.

# What the backend process must not import: supervisor-only modules. The
# spawn target and the entry callable live in alasio.backend.entry (stdlib
# only), so supervisor.py / backend.py never enter the backend process.
# The backend does import the worker skeleton (bridge / manager / event)
# on purpose: WorkerManager is how the backend spawns and manages workers.
BACKEND_BLACKLIST = {
    'alasio.backend.supervisor',
    'alasio.backend.backend',
    'alasio.backend.mpipe.token_supervisor',
}

# What the supervisor process must not import: the backend web stack,
# worker content (bridge / manager / event under alasio.backend.worker),
# msgspec and the logger.
SUPERVISOR_BLACKLIST = {
    'starlette', 'hypercorn', 'trio', 'anyio', 'h11',
    'alasio.backend.worker',
    'msgspec',
    'alasio.logger',
}

# What the worker process must not import: the backend web stack,
# supervisor-only modules, the backend process entry (alasio.backend.entry)
# and worker.manager. The worker runs on bridge (its spawn target module)
# and event only: manager belongs to the backend (BACKEND_WORKER_MANAGER).
# logger / msgspec are worker's own dependencies (shared with backend), so
# they are not blacklisted here.
WORKER_BLACKLIST = {
    'starlette', 'hypercorn', 'trio', 'anyio', 'h11',
    'alasio.backend.supervisor',
    'alasio.backend.backend',
    'alasio.backend.mpipe.token_supervisor',
    'alasio.backend.entry',
    'alasio.backend.worker.manager',
}


def _load_gui_module_level():
    """
    Re-execute gui.py at module level, equivalent to the spawn child
    re-import of the main script (skips the __main__ block).
    """
    spec = importlib.util.spec_from_file_location('gui', GUI_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)


def import_supervisor_startup():
    """
    Simulate supervisor process startup imports: gui.py module level plus
    the __main__ block (BackendWithSupervisor instantiation).
    """
    _load_gui_module_level()
    from alasio.backend.backend import BackendWithSupervisor
    BackendWithSupervisor().multiprocessing_freeze_support()


def import_backend_startup():
    """
    Simulate backend process startup imports: spawn main-script re-import,
    pickle target / args deserialization, entry body, server layer
    (alasio.backend.app.asgi), app module level and the worker manager chain
    (lifespan).
    """
    # spawn re-import of the main script, module level only
    _load_gui_module_level()
    # pickle target deserialization (imported for its side effect only)
    # serve_app -> hypercorn
    from hypercorn.trio import serve  # noqa: F401

    # serve_app -> app.py module level (lazy, after create_config set the root)
    import alasio.backend.app.app  # noqa: F401
    # backend_entry -> asgi.py module level (the backend entry module)
    from alasio.backend.app.asgi import run  # noqa: F401
    # spawn args deserialization (backend_entry callable)
    from alasio.backend.entry import backend_entry  # noqa: F401
    from alasio.backend.entry import backend_process_entry  # noqa: F401
    # entry body: token seed
    from alasio.backend.mpipe.token_backend import token_table  # noqa: F401
    # lifespan cleanup chain (BACKEND_WORKER_MANAGER)
    from alasio.backend.worker.manager import WorkerManager  # noqa: F401


def import_worker_startup():
    """
    Simulate worker process startup imports: spawn main-script re-import,
    pickle target (bridge), mod_entry body (env / logger / msgpack) and the
    mod runtime config chain.
    """
    _load_gui_module_level()
    from msgspec.msgpack import Encoder  # noqa: F401

    import alasio.backend.worker.bridge  # noqa: F401
    # mod runtime config chain
    import alasio.config.base.config_access  # noqa: F401
    import alasio.config.entry.mod  # noqa: F401
    import alasio.config.table.scan  # noqa: F401
    # mod_entry body: env / logger / msgpack
    from alasio.ext import env  # noqa: F401
    from alasio.logger import logger  # noqa: F401


class TestProcessImportIsolation:
    """
    Each process layer must not import the exclusive content of the other
    layers at startup.
    """

    def test_supervisor_no_backend_worker_imports(self):
        """
        Supervisor startup must not import the backend web stack, worker
        content (msgspec) or the logger.
        """
        run = HeavyImportTest(
            SUPERVISOR_BLACKLIST,
            'Supervisor process',
        )
        run.run_test(import_supervisor_startup)

    def test_backend_no_supervisor_imports(self):
        """
        Backend startup must not import supervisor-only modules: the spawn
        target and the entry callable live in alasio.backend.entry.
        """
        run = HeavyImportTest(
            BACKEND_BLACKLIST,
            'Backend process',
        )
        run.run_test(import_backend_startup)

    def test_worker_no_backend_supervisor_imports(self):
        """
        Worker startup (including the mod runtime config chain) must not
        import the backend web stack or supervisor-only modules.
        """
        run = HeavyImportTest(
            WORKER_BLACKLIST,
            'Worker process',
        )
        run.run_test(import_worker_startup)
