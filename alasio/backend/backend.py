# Backend entry file should not have any global import
# otherwise every child process will import them in spawn mode

from alasio.backend.supervisor import Supervisor


class BackendSupervisor(Supervisor):
    @staticmethod
    def backend_entry(args):
        from alasio.backend.app import run
        run(args)
