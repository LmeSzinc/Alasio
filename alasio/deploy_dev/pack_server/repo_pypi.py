"""
Write the pages of the pip index of the wheels a repo provides.

The wheels of the provided pins are synced to the folder a client reads by
repo_wheel:

    {workspace}/wheel/{name}/{version}/{filename}.whl

This step writes the pages on top of them:

    {workspace}/wheel/{name}/index.html   the PEP 503 page of a name
    {workspace}/wheel/index.html          the find-links page of the folder
    {workspace}/wheel/flat.html           the page a person reads

A page lists the wheels of the run and links every one with its `#sha256=`, so
pip verifies what it downloads, and the pages are written from the run rather
than read back from the folder, so a page never lists a file the folder does
not hold.

The three pages are three readers:

- the page of a name is the simple index of the distribution, the shape pip
  reads as `--index-url .../{name}/`;
- the page of the folder is the find-links page of the whole index, the shape
  pip reads as `--find-links .../index.html`: its links start at the name;
- the flat page is the page a person opens, one section a distribution and a
  matrix a compiled version. It is not a page pip needs, and it is not on a
  PEP 503 path, so a change of the layout of the flat page never invalidates
  what a client cached of the two others.

The pages of a name the run did not publish are removed by the cleanup of the
run (RepoBase.cleanup), like every other file of the workspace the run did not
touch: a name that leaves the provided set takes its folder with it.

The step runs with the publication barrier, see repo.Repo.commit(): a page
lists the wheels of a version, and a client of the previous version must keep
reading the index of the version it runs until it switches.

Usage:
    from alasio.deploy_dev.pack_server.repo_base import RepoBase
    from alasio.deploy_dev.pack_server.repo_wheel import RepoWheel
    from alasio.deploy_dev.pack_server.repo_pypi import RepoPypi

    base = RepoBase(repo, config.data)
    wheel = RepoWheel(base)
    wheel.run()
    pypi = RepoPypi(base, wheel)
    # after every pack of the version is on the disk, before latest.pack
    pypi.run()
"""

from alasio.codegen.pypisite import WheelLink, find_links_site, simple_site
from alasio.codegen.pypisite.flat import flat_site
from alasio.logger import logger

__all__ = [
    'FLAT_PAGE',
    'INDEX_PAGE',
    'RepoPypi',
]

# the page of a name, the simple index of the distribution
INDEX_PAGE = 'index.html'

# the page of the folder a person reads, next to the find-links page and not a
# PEP 503 path: a change of its layout invalidates nothing a client cached
FLAT_PAGE = 'flat.html'


class RepoPypi:
    """
    Write the pip index pages of the wheel folder, see the module docstring.

    Usage:
        pypi = RepoPypi(base, wheel)
        pypi.run()
        pypi.pages   # the pages of the run, the files the cleanup keeps
    """

    def __init__(self, base, wheel, find_links_url=None):
        """
        Args:
            base (RepoBase): Workspace and declarations of the repo
            wheel (RepoWheel): The step that published the wheels, its
                publish_files is the input of the pages
            find_links_url (str, optional): Url the folder of the wheels is
                served at, written into the command of the flat page. None for
                a run that does not know it, the page then writes no command.
                Defaults to None
        """
        self.base = base
        self.wheel = wheel
        self.find_links_url = find_links_url
        # the pages written by run(), the files the run keeps
        self.pages = []

    def run(self):
        """
        Write the pages of the run: one a name, one the folder, one the reader.

        A run that publishes no wheel writes no page: the pages of the previous
        run are removed by the cleanup of the version, so an index of a set of
        names this version does not provide does not outlive it.

        Raises:
            OSError: If a page cannot be written
        """
        self.pages = []
        groups = self._groups()
        if not groups:
            logger.info(
                f'No provided dependency of "{self.base.workspace.name}", '
                f'no pip index page is written')
            return
        pages = self._pages(groups)
        logger.info(
            f'Publishing the pip index pages of "{self.base.workspace.name}": '
            f'{", ".join(groups)}')
        for file, gen in pages:
            # the generator writes the page, and only when it changed: the
            # steps of a write are the ones of every generator of codegen
            written = gen.write(file)
            # a page the run did not change is a page the run uses: the cleanup
            # of the version keeps what the run touches, whether it wrote it or
            # found it current
            self.base.touch(file)
            if written:
                logger.info(f'Write page: "{file}"')
            else:
                logger.info(f'Page exists, skipped: "{file}"')
            self.pages.append(file)

    def _groups(self):
        """
        The wheels of the run, grouped by the distribution they belong to.

        The names are sorted: the pages do not depend on the order the pins of
        the dependency file are read in, so a rerun of the same pins with the
        file reordered writes the same bytes.

        Returns:
            dict[str, list[WheelLink]]: {PEP 503 name: the wheels of it}

        Raises:
            ValueError: If a wheel of the run is not a link of the page, see
                WheelLink
        """
        groups = {}
        for (name, version), wheels in self.wheel.publish_files.items():
            groups.setdefault(name, []).extend(
                WheelLink(name, version, wheel.file.name, wheel.hashes['sha256'])
                for wheel in wheels)
        return {name: groups[name] for name in sorted(groups)}

    def _pages(self, groups):
        """
        The pages of the run, in the order they are written.

        A page is a generator, not a text: the generator is what writes it, and
        it writes only when the page changed.

        Args:
            groups (dict[str, list[WheelLink]]): The wheels, see _groups

        Returns:
            list: [(file, generator)] of the pages
        """
        folder = self.base.wheel_folder
        pages = [
            (folder.joinpath(name).joinpath(INDEX_PAGE), simple_site(name, links))
            for name, links in groups.items()
        ]
        links = [link for group in groups.values() for link in group]
        title = self._title()
        pages.append((folder.joinpath(INDEX_PAGE), find_links_site(links, title=title)))
        pages.append((folder.joinpath(FLAT_PAGE), flat_site(groups, title, command=self._command())))
        return pages

    def _title(self):
        """
        The title of the pages of the whole folder.

        Returns:
            str: e.g. 'Wheels of Author_Repo_master'
        """
        return f'Wheels of {self.base.workspace.name}'

    def _command(self):
        """
        The command a reader of the flat page copies.

        Returns:
            str: The pip install line of the folder, None when the run does not
                know the url the folder is served at
        """
        if not self.find_links_url:
            return None
        return f'pip install --no-index --find-links {self.find_links_url} <package>'
