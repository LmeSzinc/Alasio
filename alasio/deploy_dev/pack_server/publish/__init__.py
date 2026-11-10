"""
The uploader and the cleaner of the pack server: the two consumers of the
manifest a generation writes.

A generation writes one workspace per repo
(``{run}/pack/{Author}_{Repo}_{Branch}``), with a published contract next to
its files::

    {workspace}/manifest.json    the files a mirror serves (Manifest)
    {workspace}/packrepo/latest.pack   the barrier a client switches the version at

The publish flow mirrors that workspace to every static host the config of the
repo names, and removes what a later generation no longer serves:

- upload.py writes the files of the manifest, then the barrier, then the
  manifest, so a mirror that has the manifest has the whole generation;
- cleanup.py reads the manifest and the barrier of ONE remote, and only then
  lists it: the keep set and the age of the generation come from the same
  generation, and a half written one is never deleted.

Neither of them knows what the host is: both work on the methods of
``alasio.deploy_dev.store.ObjectStore``.
"""

from alasio.deploy_dev.pack_server.model import ConfigError
from alasio.deploy_dev.pack_server.publish.common import (
    BARRIER_KEY, MANIFEST_KEY, PublishError, PublishRunError, ancestor_folders, cache_control, content_type, is_no_cache
)
from alasio.deploy_dev.pack_server.publish.drivers import DRIVERS, Driver, create_store, driver_name, workspace_prefix
from alasio.deploy_dev.pack_server.publish.env import env_value
from alasio.deploy_dev.pack_server.publish.lock import LOCK_FOLDER, ChannelBusy, ChannelLock

__all__ = [
    'BARRIER_KEY',
    'DRIVERS',
    'LOCK_FOLDER',
    'MANIFEST_KEY',
    'ChannelBusy',
    'ChannelLock',
    'ConfigError',
    'Driver',
    'PublishError',
    'PublishRunError',
    'ancestor_folders',
    'cache_control',
    'content_type',
    'create_store',
    'driver_name',
    'env_value',
    'is_no_cache',
    'workspace_prefix',
]
