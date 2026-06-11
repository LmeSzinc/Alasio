from alasio.backport.patch import patch_startup

patch_startup()

if __name__ == '__main__':
    # run
    from alasio.backend.backend import BackendSupervisor

    supervisor = BackendSupervisor().multiprocessing_freeze_support()
    supervisor.run_gui()
