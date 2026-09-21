"""
Mod loading probe.

Started by tests/backend/app/test_mod_loading.py in a fresh interpreter with
--root <project root>. It walks the backend child startup order:

1. import the server layer (alasio.backend.app.asgi) -- what entry.backend_entry
   does, before any project root is known;
2. set the project root the way create_config does
   (env.set_project_root + os.chdir; create_config itself is covered by
   test_root_propagation);
3. import the ASGI app lazily (what asgi.serve_app does) and build it with
   create_app() -- the point where mods are read from the loader and their
   asset folders are mounted.

Importing the app chain before step 2 freezes MOD_LOADER.root to an empty
root (MOD_LOADER = ModLoader(env.PROJECT_ROOT), bound at import time, see
alasio/backend/app/asgi.py): a mod declared by the project root itself
(module/config/const.py, loaded by ModLoader.self_mod) then resolves its own
root to '' and worker_start() is handed an empty mod_root.

The probe prints one `MOD_REPORT=<json>` line (pure ASCII) for the test to
parse, everything else on stdout is logger noise.
"""

import argparse
import json
import os
import sys

from alasio.ext import env


def parse_args():
    """
    Returns:
        argparse.Namespace: Parsed arguments, `root` is the project root
    """
    parser = argparse.ArgumentParser(description='Mod loading probe')
    parser.add_argument('--root', type=str, required=True)
    parsed_args, _ = parser.parse_known_args()
    return parsed_args


def iter_mounts(routes, prefix=''):
    """
    Walk a starlette route tree and yield every mount.

    Args:
        routes (Iterable): Routes to walk
        prefix (str): Path prefix of the parent mount

    Yields:
        tuple[str, str | None]: Full mount path and the mounted directory
            (None when the mount is not a static files app)
    """
    for route in routes:
        path = f'{prefix}{getattr(route, "path", "")}'
        app = getattr(route, 'app', None)
        sub_routes = getattr(route, 'routes', None)
        if sub_routes:
            yield from iter_mounts(sub_routes, path)
        else:
            yield path, getattr(app, 'directory', None)


def collect_mod(mod):
    """
    Collect the loading facts of one mod.

    Args:
        mod (Mod):

    Returns:
        dict: JSON-serializable facts about the mod
    """
    entry = mod.entry
    return {
        'name': mod.name,
        'root': str(mod.root),
        'path_config': str(mod.path_config),
        'path_assets': str(mod.path_assets),
        'exist': bool(entry.exist()),
        'path_main': entry.path_main,
        'path_main_exists': bool(os.path.isfile(mod.root.joinpath(entry.path_main))),
        'nav_names': sorted(mod.nav_index_data().keys()),
        'config_names': sorted(mod.config_index_data().keys()),
        'task_count': len(mod.task_index_data()),
        'queue_tasks': sorted(mod.queue_index_data().keys()),
        # the arguments BackendWorkerManager.worker_start() builds: a falsy
        # one makes the manager fall back to the "test mod" spawn branch and
        # the worker dies with KeyError('No such mod to run ...')
        'worker_start_args': {
            'project_root': str(env.PROJECT_ROOT),
            'mod_root': str(mod.root),
            'path_main': entry.path_main,
        },
    }


def build_report(args):
    """
    Args:
        args (argparse.Namespace): Parsed command line

    Returns:
        dict: Probe report
    """
    # 1. the production entry imports the server layer before any root exists
    import alasio.backend.app.asgi  # noqa: F401

    loader_imported_before_root = 'alasio.config.entry.loader' in sys.modules

    # 2. set the project root (the backend child reaches this point with an
    # unset root: entry.backend_entry imports the entry module first)
    root = args.root
    env.set_project_root(root)
    os.chdir(root)

    # 3. the lazy app import of asgi.serve_app, then the app build
    from alasio.backend.app.app import create_app
    app = create_app()

    from alasio.config.entry.loader import MOD_LOADER

    mods = {}
    for mod in MOD_LOADER.dict_mod.values():
        try:
            mods[mod.name] = collect_mod(mod)
        except Exception as e:
            # report the failure instead of dying: the test asserts on the
            # facts, a broken mod shows up as an error entry
            mods[mod.name] = {'error': f'{type(e).__name__}: {e}'}

    self_mod = MOD_LOADER.self_mod
    return {
        'root': root,
        'project_root': str(env.PROJECT_ROOT),
        'loader_root': str(MOD_LOADER.root),
        'loader_imported_before_root': loader_imported_before_root,
        'self_mod': {'name': self_mod.name, 'root': str(self_mod.root)} if self_mod else None,
        'mods': mods,
        'mounts': sorted((path, str(directory)) for path, directory in iter_mounts(app.routes)
                         if directory is not None),
    }


def main():
    report = build_report(parse_args())
    # ASCII only, so the test can decode the captured output with any encoding
    print(f'MOD_REPORT={json.dumps(report, sort_keys=True)}', flush=True)


if __name__ == '__main__':
    main()
