"""
Generate the workspace of one repo of the pack server: the flow of PackRepoModel.

One run generates one workspace, the folder of the repo in the pack folder of
the run directory. The steps are the ones of PackRepoModel, one module each,
and this module is the flow itself: it runs the steps in the order their
results depend on each other, and it publishes the version when everything is
on the disk.

    1. the workspace, the folder of the repo, is pack/{Author}_{Repo}_{Branch}
       (see RepoBase)
    2. the wheels of the pins the repo provides are downloaded to the private
       cache `.wheel_cache/{name}/{version}/` and synced to the pip index
       `wheel/{name}/{version}/` (see repo_wheel), the files a client installs
       when it cannot reach the index the pin came from
    3. the pure python wheel of every version the dependency packs are built of
       is downloaded to the same cache, and the packs of the dependencies are
       built to `packdep/{name}/{version}/` (see repo_dep): the latest commit
       and the lookback commits alike
    4. the packs of the repo tree are generated to `packrepo/{commit}/` (see
       repo_pack), the full pack of the latest commit carries the dependency
       snapshot of the step above
    5. the pages of the pip index of the wheel folder are written (see
       repo_pypi): the simple page of every name, the find-links page of the
       folder, and the flat page a person reads. They are written with the
       barrier below, never before: the page of a distribution lists the
       wheels of the version latest.pack names
    6. latest.pack is written (see repo_latest), the version a client reads:
       the barrier of the run, a client that reads it finds every pack of the
       version on the disk
    7. the record of the generation is written (see repo_manifest): the
       version of the barrier, the files the run publishes (manifest.json) and
       the files it keeps private, e.g. the wheel cache (.manifest.json)
    8. every file of the workspace the run did not touch is removed
       (RepoBase.cleanup()), after the barrier, so a client that still runs the
       previous version keeps working until its next check

A step that fails fails the run, and steps 5 to 7 do not run: latest.pack keeps
naming the previous version, its index pages keep listing the wheels of it, and
the client keeps running it. The next run starts over, the atomic writes never
leave a truncated pack at its final path.

The repo itself is an input of the flow: the caller clones and fetches it
(see PackRepo) and passes it in, the flow only reads it.

Usage:
    from alasio.deploy_dev.pack_server.repo import Repo

    repo = PackRepo(config.data).run()
    gen = Repo(repo, config.data)
    # generate the workspace
    gen.run()
    # write the pip index, publish the version and clean the workspace, after
    # every pack of the version is on the disk
    gen.commit()
"""

from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.deploy_dev.pack_server.repo_base import RepoBase
from alasio.deploy_dev.pack_server.repo_dep import RepoDep
from alasio.deploy_dev.pack_server.repo_latest import RepoLatest
from alasio.deploy_dev.pack_server.repo_manifest import RepoManifest
from alasio.deploy_dev.pack_server.repo_pack import RepoPack
from alasio.deploy_dev.pack_server.repo_pypi import RepoPypi
from alasio.deploy_dev.pack_server.repo_wheel import RepoWheel
from alasio.ext.path.atomic import atomic_failure_cleanup
from alasio.logger import logger


class Repo:
    """
    Generate the workspace of one repo, see the module docstring.

    The steps are attributes of the instance, so a caller can read what the run
    generated: the dependency snapshots of repo_dep, the packs of repo_pack.
    Build a new instance for every run, the next run must see the new commits
    of the repo.

    Usage:
        gen = Repo(repo, config.data)
        gen.run()
        gen.commit()
    """

    def __init__(self, repo, config, client=None, find_links_url=None):
        """
        Args:
            repo (GitRepo | MockGitRepo): Git repo of the config, cloned and
                fetched by the caller; the flow only reads it
            config (PackRepoModel): Config of the repo, read from a
                PackRepoConfig: the Lookback group is the window, the rest is
                declared by the mod entry of the repo
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client
                the wheel fetches are sent with, e.g. the injected client of a
                test. Its lifetime belongs to the caller. Defaults to None, the
                steps create the client of their own, through the proxy of the
                config Env when it is set (see RepoBase.fetch_client)
            find_links_url (str, optional): Url the folder of the wheels is
                served at, written into the command of the flat page, e.g.
                'https://packs.example/pack/Author_Repo_master/wheel'. Defaults
                to None, the page then writes no command

        Raises:
            ValueError: If the config makes a folder name that is not a single
                safe path component, see RepoBase.workspace
        """
        self.base = RepoBase(repo, config, client)
        self.wheel = RepoWheel(self.base)
        self.pypi = RepoPypi(self.base, self.wheel, find_links_url=find_links_url)
        self.dep = RepoDep(self.base)
        self.pack = RepoPack(self.base, self.dep)
        self.latest = RepoLatest(self.base, self.pack)
        self.manifest = RepoManifest(self.base, self.latest)
        # whether run() completed, the precondition of commit(): a failed run
        # must not publish a version whose packs are incomplete
        self._generated = False

    def run(self):
        """
        Generate the workspace: the wheels, the pip index, the packs.

        Nothing that a client reads is replaced here: latest.pack still names
        the previous version until commit() runs, and the packs of a version
        are identified by the commit they were built of.

        Raises:
            RunDirError: If env.PROJECT_ROOT is not a run directory of the
                pack server, see check_run_dir
            ValueError: If the repo has no branch to pack, the mod entry or a
                dependency file of the repo cannot be read, or a wheel is not a
                pack target (see PackWheel)
            WheelNotFoundError: If a wheel of a dependency cannot be fetched,
                the target and the historical versions alike: every update
                path of the published channel must be complete
            httpx2.HTTPError: If a wheel cannot be fetched
            OSError: If a wheel or a pack cannot be written
        """
        self._generated = False
        check_run_dir()
        # the tmp files of an interrupted run are ours to clean up, the real
        # files of the previous run keep serving the clients
        atomic_failure_cleanup(self.base.wheel_cache_folder, recursive=True)
        atomic_failure_cleanup(self.base.packdep_folder, recursive=True)
        atomic_failure_cleanup(self.base.packrepo_folder, recursive=True)

        # the pins the repo provides, logged before the steps: the set is the
        # input of the pip index and does not depend on the pack channel
        provided = [
            (name, info.latest) for name, info in self.base.deps.items() if info.provide_wheel]
        if provided:
            count = len(provided)
            plural = 's' if count > 1 else ''
            logger.info(
                f'Provided {count} pin{plural} of "{self.base.workspace.name}": '
                + ', '.join(f'{name}=={pin.version}' for name, pin in provided))

        # 1. the wheels of the provided pins, the files the index is written of
        self.wheel.run()
        # 2. the wheels of the dependency packs and the packs themselves, the
        # snapshots go to the tree pack
        self.dep.run()
        # 3. the packs of the repo tree
        self.pack.run()
        self._generated = True

    def commit(self):
        """
        Publish the version and remove the entries the window does not ask for.

        The pages of the pip index and latest.pack are written first: both are
        what a client reads, and a client of the previous version must not read
        an index of this one. The index page of a distribution lists the wheels
        of the version latest.pack names, so a run that failed before this
        point leaves both as they were, and the client keeps installing the
        version it runs.

        The manifest comes after the barrier: it names the version a client
        reads from latest.pack and the files of the run, which is exactly what
        a mirror mirrors and what the cleanup below keeps. The local record is
        written first, the published contract last, so the file a mirror serves
        is the last file of a generation to land on the disk. Only after both
        are the leftover packs and wheels removed, so a client that still runs
        the previous version keeps working until its next check.

        Raises:
            RuntimeError: If run() was not called
            OSError: If a page, latest.pack or the manifest cannot be written
        """
        if not self._generated:
            raise RuntimeError('Repo.run() must be called before commit()')
        # the pages of the wheels of the version, then the publication barrier:
        # a client that reads the new latest.pack finds the index of it too
        self.pypi.run()
        self.latest.run()
        # the record of the generation, after the barrier it names and before
        # the cleanup it is the record of: the local half first, the published
        # contract last
        self.manifest.run()
        self.base.cleanup()
        self._generated = False
        logger.info(f'Repo "{self.base.workspace.name}" committed')
