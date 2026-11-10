"""
From a configured remote to the ObjectStore of one workspace.

A store is bound to one workspace: every key it takes and returns is relative to
it, so the publication never builds a physical path and a wrong
slash cannot move a file into the workspace next to it (see store.cos.CosStore).

The dispatch is `DRIVERS[type(remote)]`: msgspec already picked the variant from
the `Driver` key, so the type is the answer, and a driver is never guessed from
a field (`hasattr(remote, 'Bucket')`) nor from a name that has to be kept in
sync with the variant.

A driver reads its own credentials: `create()` gets the remote of the config as
it is, not a dict of values, and asks the environment for the few variables it
needs. The variant therefore holds every setting of a remote, and the flow passes
one object around.

Only `cos` is implemented. The webdav entry exists in the config model so a file
that mentions one is not rewritten with that entry dropped, and its driver is the
next step of the plan: the factory refuses it with a message that says so, and
nothing else in the flow knows the difference.
"""

from alasio.deploy_dev.pack_server.model import ConfigError, CosRemote
from alasio.deploy_dev.pack_server.publish.env import env_value
from alasio.deploy_dev.store.cos import CosStore
from alasio.deploy_dev.store.cos.store import normalize_prefix, parse_endpoint
from alasio.ext.path.validate import validate_filename


def driver_name(remote):
    """
    The name of the driver of a remote, the tag of its variant.

    The name is read from the msgspec config of the class, which is the same
    value the file says in its `Driver` key: no attribute has to be kept in sync
    with the variant, and a variant that is not a driver yet still has a name.

    Args:
        remote (RemoteBase): The configured remote

    Returns:
        str: 'cos' or 'webdav'
    """
    return type(remote).__struct_config__.tag


def workspace_prefix(remote, workspace):
    """
    The key prefix of a workspace on a remote, as the store sees it.

    The workspace name is a single path component of its own (the generator
    checked it when it created the folder), and the prefix of the remote may be
    empty or have slashes: the two are joined with exactly one slash.

    Args:
        remote (RemoteBase): The configured remote
        workspace (str): Name of the workspace

    Returns:
        str: E.g. 'pack/LmeSzinc_Repo_master', '' for the bucket root

    Raises:
        ValueError: If the workspace name is not a single path component
    """
    validate_filename(workspace)
    parts = [part.strip('/') for part in (getattr(remote, 'Prefix', ''), workspace)]
    return '/'.join(part for part in parts if part)


class Driver:
    """
    What one variant of RemoteConfig needs from its store.

    A driver is a singleton in DRIVERS: it holds no state of a run, it only
    knows how to build a store for a workspace, reading the credentials the
    variant names from the environment. Every check the remote needs is made by
    create(), before the first request: a separate validate() pass would only
    walk the same fields twice.
    """

    # the tag of the variant, for a message
    name = ''
    # the variant this driver serves, the key of DRIVERS
    config_type = None
    # the environment prefix of the credentials, overridden by EnvPrefix
    env_prefix = ''

    def create(self, name, remote, workspace, client=None):
        """
        Build the store of one workspace on one remote, refusing a remote that
        cannot be served.

        Args:
            name (str): Name of the remote in the config, for the message
            remote (RemoteBase): The configured remote, its credentials are
                read from the environment here
            workspace (str): Name of the workspace, the last part of the prefix
            client (httpx2.AsyncClient): Injected client, for a test that runs
                the store against an in-process service. Defaults to None: the
                store creates its own client

        Returns:
            ObjectStore: The store bound to the workspace

        Raises:
            ConfigError: If a field or a credential is not usable
        """
        raise NotImplementedError

    def check(self, name, remote):
        """
        Refuse a remote this driver cannot serve.

        Called by create() before it builds anything, and by a caller that
        wants to report every broken remote of a config before the first
        request of any of them.

        Args:
            name (str): Name of the remote in the config, for the message
            remote (RemoteBase): The configured remote

        Raises:
            ConfigError: If a required field is empty or not usable
        """
        raise NotImplementedError


class CosDriver(Driver):
    """
    The 'cos' variant: a Tencent COS bucket, over the project http client.
    """

    name = 'cos'
    config_type = CosRemote
    env_prefix = 'ALASIO_COS'
    capabilities = CosStore.CAPABILITIES

    def check(self, name, remote):
        """
        Refuse a remote whose container fields are not usable.

        Every field here names the container of the requests: a bucket, a
        region, a key prefix and an endpoint. They are checked with the same
        functions the store uses to build its urls, so a value the store would
        refuse is refused before the first request and by a ConfigError rather
        than by a ValueError from inside the store's constructor.

        Args:
            name (str): Name of the remote in the config, for the message
            remote (CosRemote): The configured remote

        Raises:
            ConfigError: If a field is empty or not usable
        """
        if not remote.Bucket:
            raise ConfigError(f'Publish.{name}.Bucket is empty')
        if not remote.Region:
            raise ConfigError(f'Publish.{name}.Region is empty')
        try:
            validate_filename(remote.Bucket)
        except ValueError as e:
            raise ConfigError(
                f'Publish.{name}.Bucket is not a usable name: {e}') from None
        try:
            normalize_prefix(remote.Prefix)
        except ValueError as e:
            raise ConfigError(
                f'Publish.{name}.Prefix is not usable: {e}') from None
        try:
            parse_endpoint(remote.Endpoint, remote.Bucket, remote.Region)
        except ValueError as e:
            raise ConfigError(
                f'Publish.{name}.Endpoint is not usable: {e}') from None

    def create(self, name, remote, workspace, client=None):
        """
        Check the remote, then build the store of one workspace of the bucket.

        The credentials are read here: a temporary key carries a token next to
        the pair, and a long term key has none, so only the pair is required.

        Args:
            name (str): Name of the remote in the config, for the message
            remote (CosRemote): The configured remote
            workspace (str): Name of the workspace, the last part of the prefix
            client (httpx2.AsyncClient): Injected client. Defaults to None

        Returns:
            CosStore: The store bound to the workspace

        Raises:
            ConfigError: If a field or a credential is not usable
        """
        self.check(name, remote)
        prefix = remote.EnvPrefix or self.env_prefix
        try:
            key_prefix = workspace_prefix(remote, workspace)
        except ValueError as e:
            # the workspace name is the last part of the prefix, and it is the
            # one value no field of the remote carries: the model refuses such
            # a name when it builds it, this is the same answer for a caller
            # that builds a store from a string of its own
            raise ConfigError(
                f'Publish.{name}: the workspace name is not usable: {e}') from None
        return CosStore(
            bucket=remote.Bucket,
            region=remote.Region,
            secret_id=env_value(prefix, 'SECRET_ID', required=True),
            secret_key=env_value(prefix, 'SECRET_KEY', required=True),
            token=env_value(prefix, 'SESSION_TOKEN') or None,
            endpoint=remote.Endpoint or None,
            prefix=key_prefix,
            client=client,
        )


# dispatch is DRIVERS[type(remote)], never a field sniffing
DRIVERS = {CosRemote: CosDriver()}


def create_store(name, remote, workspace, client=None):
    """
    Build the store of one workspace on one remote.

    This is the only entry of a flow to a remote: the driver checks the remote
    and reads the credentials in the same call, so there is no window in which
    a checked remote is built from unchecked values.

    Args:
        name (str): Name of the remote in the config, for the message
        remote (RemoteBase): The configured remote
        workspace (str): Name of the workspace, the last part of the prefix
        client (httpx2.AsyncClient): Injected client, for a test that runs the
            store against an in-process service. Defaults to None: the store
            creates its own client

    Returns:
        ObjectStore: The store bound to the workspace

    Raises:
        ConfigError: If the driver is not implemented, a field is empty or a
            credential is missing
    """
    driver = DRIVERS.get(type(remote))
    if driver is None:
        raise ConfigError(
            f'Publish.{name}: the {driver_name(remote)} driver is not implemented yet')
    return driver.create(name, remote, workspace, client=client)
