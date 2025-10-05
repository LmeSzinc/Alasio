from alasio.backend.supervisor import Supervisor


class Backendsupervisor(Supervisor):
    @staticmethod
    def backend_entry():
        from alasio.backend.app import run
        run()


if __name__ == '__main__':
    # For multiprocessing to work correctly on all platforms
    import multiprocessing

    multiprocessing.freeze_support()

    # run
    supervisor = Backendsupervisor()
    supervisor.run()
