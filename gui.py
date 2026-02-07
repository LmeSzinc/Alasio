from alasio.ext.backport.patch import patch_startup

patch_startup()

if __name__ == '__main__':
    # For multiprocessing to work correctly on all platforms
    import multiprocessing

    multiprocessing.freeze_support()

    # run
    from alasio.backend.backend import BackendSupervisor

    supervisor = BackendSupervisor()
    supervisor.run_gui()
