"""
Write the pip index of the wheels a repo provides (the pages of step 5).

The wheels of the provided pins are synced to the folder a client reads by
repo_wheel:

    {workspace}/wheel/{name}/{version}/{filename}.whl

This step writes the pages on top of them, the index pip reads:

    {workspace}/wheel/index.html               the find-links page of the repo
    {workspace}/wheel/{name}/index.html        the PEP 503 page of a name

A page lists the wheels of the run and links every one with its `#sha256=`, so
pip verifies what it downloads, and the pages are written from the run rather
than read back from the folder, so a page never lists a file the folder does
not hold.

The pages are not written yet. The step is in the flow so they land in the
place the design gives them, and the folder is installable in the meantime:
a client that lists `{workspace}/wheel/` with `--find-links` reads the wheels
from the directory listing of the static host.

Usage:
    from alasio.deploy_dev.pack_server.repo_base import RepoBase
    from alasio.deploy_dev.pack_server.repo_pypi import RepoPypi

    base = RepoBase(repo, config.data)
    pypi = RepoPypi(base)
    pypi.run()
"""


class RepoPypi:
    """
    Write the pip index of the wheel folder, not written yet.

    Usage:
        pypi = RepoPypi(base)
        pypi.run()
    """

    def __init__(self, base):
        """
        Args:
            base (RepoBase): Workspace and declarations of the repo
        """
        self.base = base

    def run(self):
        """
        Write the pages of the pip index.

        Nothing is written yet, see the module docstring. The step stays a
        no-op of the flow rather than a missing call in repo: the pages are the
        next work of the module, and the place they are written from is here.
        """
