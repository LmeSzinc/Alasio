from alasio.ext.patch import patch_startup

patch_startup()

if __name__ == '__main__':
    # For multiprocessing to work correctly on all platforms
    import multiprocessing

    multiprocessing.freeze_support()

    # run
    import os
    import sys
    from alasio.backend.backend import BackendSupervisor

    args = sys.argv[1:]
    args += ['--root', os.getcwd()]
    supervisor = BackendSupervisor()
    supervisor.run(args)
