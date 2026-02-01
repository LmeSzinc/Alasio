# Backend entry file should not have any global import
# otherwise every child process will import them in spawn mode

from alasio.backend.supervisor import Supervisor


class BackendSupervisor(Supervisor):
    @staticmethod
    def backend_entry(args):
        from alasio.backend.app import run
        run(args)

    def run_gui(self, args=None, root='', up=1):
        """
        Args:
            args (list[str] | None):
            root (str): input __file__ of gui.py
            up (int): Uppath from gui.py to project root
        """
        import sys
        from alasio.ext.path import PathStr

        gui_args = sys.argv[1:]
        if args:
            gui_args += args
        gui_args += ['--root', PathStr.new(root).uppath(up)]
        self.run(gui_args)
