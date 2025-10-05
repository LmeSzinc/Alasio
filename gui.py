if __name__ == '__main__':
    # For multiprocessing to work correctly on all platforms
    import multiprocessing

    multiprocessing.freeze_support()

    # run
    from alasio.backend.main import Backendsupervisor

    supervisor = Backendsupervisor()
    supervisor.run()
