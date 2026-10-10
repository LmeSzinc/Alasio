"""
Install python distributions into a site-packages folder from the pack
channel of a mod.

The dependencies of a mod are distributed as packs and applied by the same
job machine as the mod tree, see
doc/2026-09-30_python-dist-pack-update-flow.md. One pack server run
publishes the channel of the mod tree and the channel of every dependency
of the repo config next to each other (doc §10.8):

    {BaseUrl}/{Author}_{Repo}_{Branch}/packrepo       channel of the mod tree
    {BaseUrl}/{Author}_{Repo}_{Branch}/packdep/{dep}  channel of a dependency

The mirrors of the mod (mod.entry.mirrors) point at the channel base of its
tree (doc/2026-10-05_mod-update-backend-integration.md §9), so the channel
of a dependency is derived from them: the packrepo folder of every url
becomes the packdep folder of the distribution, the url and the disk of the
deployment stay isomorphic. A distribution installs into the site-packages
folder as a named deploy target of its own
(doc/2026-10-04_deploy-job-named-target.md): the ledger of its version lives
in {site_packages}/.pack/{dist-key}, next to the ledgers of the other
distributions of the environment.

Usage:
    pip = PipPack(mod, site_packages)
    await pip.install('httpx', '0.28.1')

Every install is driven by the version the caller names, the pin the
requirements file of the mod holds for the distribution: the install
requires the channel to publish exactly that version as its latest one
(DeployJob.update(version)), a deployment that publishes another version
provides no valid update and the install is refused. Every install runs on
the caller event loop (trio) and DeployJob.update(version) holds the
exclusive lock of the distribution ledger for its whole flow, so two
installs of the same distribution never interleave and an install killed
in flight is resumed by the next one.
"""

from alasio.deploy.httpclient.probe import Mirrors
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.server_file import ServerFile
from alasio.deploy.simple_pip.pip_list import normalize_name
from alasio.ext.path import PathStr
from alasio.ext.path.validate import validate_filename
from alasio.logger import logger

# Folder of the mod tree channel of a repo config, and folder of the
# dependency channels next to it, see the pack server layout: the url and
# the disk of a deployment are isomorphic, so the channel of a dependency
# is the channel of the mod tree with its last folder replaced
PACKREPO_FOLDER = 'packrepo'
PACKDEP_FOLDER = 'packdep'


class PipPack:
    """
    The dependency updates of one mod: install the python distributions of
    a site-packages folder from the pack channels of the mod.

    One instance serves every install of one target environment; the
    mirrors of the mod are read once in __init__ and the channel of a
    dependency is derived per install, see _channel_mirrors().

    The channel of a dependency keeps the mirror names and the group
    boundaries of the mod mirrors (only the urls change) and its mirror
    record (gui.db) is scoped to the mod: whichever channel of the mod
    probed first, the others reuse the winning mirror name (a name is the
    identity of a mirror, its url is read from the mirrors at every use,
    see ServerUrl).

    Attributes:
        mod (Mod): The mounted mod, its entry carries the update source
            (name and entry.mirrors are read)
        site_packages (PathStr): Folder the distributions are installed
            into, the root of the deploy jobs of every dependency
        client (httpx2.AsyncClient | AsyncHttpClient | None): Client the
            pack requests are sent with, None to create one per install
        mirrors (Mirrors): Mirror structure of the mod channel, the base
            every dependency channel is derived from
    """

    def __init__(self, mod, site_packages, client=None):
        """
        Args:
            mod (Mod): The mounted mod whose mirrors point at the channel
                base of its tree
            site_packages (str | PathStr): Path of the site-packages folder
                to install the distributions into
            client (httpx2.AsyncClient | AsyncHttpClient, optional): Client
                the pack requests are sent with, e.g. the process client of
                the caller. Its lifetime belongs to the caller, it is never
                closed here. Defaults to None, every install creates a
                server with a client of its own and closes it

        Raises:
            ValueError: The mod declares no update source, or the declared
                one is not a usable mirror structure
        """
        self.mod = mod
        self.site_packages = PathStr.new(site_packages)
        self.client = client
        mirrors = mod.entry.mirrors
        if not mirrors:
            raise ValueError(f'The mod declares no update source: "{mod.name}"')
        self.mirrors = Mirrors.from_input(mirrors)

    async def install(self, name, version):
        """
        Install a distribution at a version into the site-packages folder,
        from the pack channel of the mod.

        The version is the pin the requirements file of the mod holds for
        the distribution: the install verifies that the channel publishes
        exactly that version as its latest one (DeployJob.update(version)
        checks it), a deployment that publishes another version provides no
        valid update and the install is refused, nothing of it is applied
        then. Otherwise the flow is the unified one of DeployJob.update():
        the local ledger version is compared with the version, the same
        version is verified and repaired against the published index
        (ResetJob), a version mismatch is applied incrementally from
        /{version}/from_{local}.pack (UpdateJob), a missing update pack or
        local ledger falls back to a rebuild from the index of the version
        (RebuildJob). The channel of a dependency publishes the packs of its
        latest version only (the pins of the latest commit of the repo built
        it), which is exactly the version the check accepted.

        Args:
            name (str): Name of the distribution, e.g. 'httpx' or
                'ruamel.yaml', any case and separator style (the PEP 503
                normalized name is the channel folder and the ledger key)
            version (str): Display version to install, e.g. '0.28.1' (the
                Version of the METADATA of the wheel), the pin of the
                requirements file of the mod

        Returns:
            bool: True when every recorded file is at the version, False
                when some files stayed in error (the next install retries
                them)

        Raises:
            ValueError: The version is empty or not a single safe path
                component, the name is not a usable distribution name, or
                the channel does not publish the requested version
                (nothing of the install is applied in the last case)
            httpx2.HTTPError: If a request fails outside the rebuild
                fallbacks
            AllMirrorsFailedError: If no mirror of the channel is usable
        """
        if not version:
            raise ValueError(f'Failed to install "{name}": the version is empty')
        dist_key = normalize_name(name)
        # the dist-key names the channel folder and the ledger folder of the
        # distribution, only a single safe path component can be one; the
        # version names the pack folder of the channel and is embedded in
        # the urls of the flow, the same rule applies
        validate_filename(dist_key)
        validate_filename(version)
        server = ServerFile(
            self._channel_mirrors(dist_key),
            # the record is shared with the channel of the mod tree: the
            # winning mirror name of either is reused by the other, see the
            # class docstring
            scope=self.mod.name,
            client=self.client,
        )
        try:
            job = DeployJob(root=self.site_packages, name=dist_key, server=server)
            logger.info(f'Installing "{name}=={version}" to "{self.site_packages}"')
            ok = await job.update(version=version)
        finally:
            # the client a server created for itself goes with the install;
            # an injected client belongs to the caller and is never closed
            await server.aclose()
        if ok:
            logger.info(f'Installed "{name}=={version}"')
        else:
            logger.warning(f'Failed to install "{name}=={version}": some files stayed in error')
        return ok

    def _channel_mirrors(self, dist_key):
        """
        The mirror structure of the pack channel of one dependency.

        The pack server publishes every dependency of a repo config next to
        the channel of its mod tree (packrepo -> packdep/{dep}), the two
        folders are siblings on the disk and in the url. The structure of
        the mod mirrors is kept as it is (the groups, the mirror names and
        their declared order), only the urls change: the channel probes,
        falls back and records its mirror exactly like the mod channel, and
        the mirror record of either is reused by the other (the structure
        fingerprint and the scope are the same, see ServerUrl).

        Args:
            dist_key (str): PEP 503 normalized distribution name

        Returns:
            Mirrors: The mirrors of the channel

        Raises:
            ValueError: If a mirror url does not point at a packrepo channel
                of a deployment
        """
        return Mirrors({
            group: {
                name: self._dep_url(url, dist_key)
                for name, url in members.items()
            }
            for group, members in self.mirrors.groups.items()
        })

    def _dep_url(self, url, dist_key):
        """
        The url of one mirror of a dependency channel.

        Args:
            url (str): Url of a mirror of the mod channel, ending with the
                packrepo folder
            dist_key (str): PEP 503 normalized distribution name

        Returns:
            str: Url of the same mirror for the packdep folder of the
                distribution

        Raises:
            ValueError: If the url does not end with the packrepo folder
        """
        root, sep, folder = url.rpartition('/')
        if not sep or folder != PACKREPO_FOLDER:
            raise ValueError(
                f'The update source of "{self.mod.name}" does not point at a '
                f'{PACKREPO_FOLDER} channel, cannot derive the channel of '
                f'"{dist_key}": {url!r}'
            )
        return f'{root}/{PACKDEP_FOLDER}/{dist_key}'
