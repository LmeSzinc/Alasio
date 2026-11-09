"""
The values the uploader and the cleaner share: the errors, the keys of the
record, and the per-path headers a mirror serves.

Nothing here knows about a host: the module is imported by both flows and by the
stores' users, and it depends on the standard library alone.
"""

import re

try:
    BaseExceptionGroup
except NameError:  # python 3.8 / 3.9
    from exceptiongroup import BaseExceptionGroup

from alasio.deploy_dev.pack_server.manifest import MANIFEST_FILE
from alasio.deploy_dev.pack_server.repo_latest import LATEST_FILE

# the relative key of the published contract and of the barrier, inside a
# workspace. The uploader writes the barrier last but one, the contract last;
# the cleaner reads both before it lists.
MANIFEST_KEY = MANIFEST_FILE
BARRIER_KEY = f'packrepo/{LATEST_FILE}'

# the transfers (and local reads) one channel runs at once. A cap of the tool
# rather than of the config: past it the queue only moves into the connection
# pool, and the uplink of the publishing machine is the real limit
MAX_CONCURRENCY = 8

# the shape of an MD5 in a hex string: the ETag of a single PUT is one, and a
# store that declares etag_md5 can be trusted only while its ETag has the shape
MD5_HEX = re.compile(r'^[0-9a-f]{32}$')

# the content types of the files a workspace holds, by suffix. A pack, a wheel
# and an archive are served to a program; the html is a page a browser renders
CONTENT_TYPES = {
    '.json': 'application/json; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
}

DEFAULT_CONTENT_TYPE = 'application/octet-stream'

# the cache policy of an immutable file, and of one that must be read back. A
# pack of a version never changes, so it is cached for a year; the manifest and
# the barrier name the current generation, so a cached copy would pin a client
# to a version the server no longer serves
IMMUTABLE_CACHE = 'public, max-age=31536000, immutable'
NO_CACHE = 'no-cache'

# the suffixes that are never cached, whatever their name. A '.json' or an
# '.html' file of a workspace names a generation (the manifest, a page of the
# wheel index); a pack is content, even the one the barrier sits in, because the
# barrier is matched by its key above
NO_CACHE_SUFFIXES = ('.json', '.html')


class PublishError(Exception):
    """
    Raised when a publish step fails: a guard of the flow refused the
    generation, a store refused a request after its retries, or a write did not
    land as it was sent.

    The message names the channel, the remote and the key; it never holds a
    credential.
    """


class SkipChannel(Exception):
    """
    Raised when a guard refuses the generation of a workspace.

    A skip is not a failure: the mirror keeps the previous generation, the
    uploader writes nothing, and the next run of the generator fixes the cause.
    The message is the reason and is logged once. It is deliberately not a
    PublishError: a caller that handles the publish errors must not count a
    refused generation as a failed mirror.
    """


class ConfigError(PublishError):
    """
    Raised when publish.yaml or a credential is not usable: an unknown key, a
    remote of a driver that does not exist, a missing environment variable.

    A caller that runs a batch reads it as "this remote is not configured",
    not as "the run failed at the network".
    """


class PublishRunError(PublishError):
    """
    Raised at the end of a run in which at least one channel or remote failed.

    The failures are reported one by one as they happen, and the run keeps
    going: one unreachable mirror must not stop the publication to the others.

    Attributes:
        failed (list): (channel, remote, reason) of every failed pair
    """

    def __init__(self, failed):
        """
        Args:
            failed (list): (channel, remote, reason) of every failed pair
        """
        self.failed = list(failed)
        detail = ', '.join(f'{channel}@{remote}: {reason}' for channel, remote, reason in self.failed)
        super().__init__(f'{len(self.failed)} channel/remote pair(s) failed: {detail}')


def content_type(relative):
    """
    The Content-Type a mirror serves a file with.

    Args:
        relative (str): '/'-separated key relative to the workspace

    Returns:
        str: MIME type of the file, an octet stream for an unknown suffix
    """
    suffix = '.' + relative.rpartition('.')[2].lower() if '.' in relative.rpartition('/')[2] else ''
    return CONTENT_TYPES.get(suffix, DEFAULT_CONTENT_TYPE)


def is_no_cache(relative):
    """
    Whether a file must not be cached by a client or a CDN.

    The manifest, the barrier and the html index name the current generation: a
    cached copy would keep a client on a version that is no longer served, or
    hide a new one for the lifetime of the cache.

    Args:
        relative (str): '/'-separated key relative to the workspace

    Returns:
        bool: True when the file is served with no-cache
    """
    if relative in (MANIFEST_KEY, BARRIER_KEY):
        return True
    name = relative.rpartition('/')[2]
    suffix = '.' + name.rpartition('.')[2].lower() if '.' in name else ''
    return suffix in NO_CACHE_SUFFIXES


def cache_control(relative):
    """
    The Cache-Control a mirror serves a file with.

    Args:
        relative (str): '/'-separated key relative to the workspace

    Returns:
        str: 'no-cache' for the files that name a generation, an immutable
            policy for the content of one
    """
    return NO_CACHE if is_no_cache(relative) else IMMUTABLE_CACHE


def ancestor_folders(relative):
    """
    The folders a key lives in, deepest first.

    Args:
        relative (str): '/'-separated key, e.g. 'packrepo/v1/full.pack'

    Returns:
        list[str]: ['packrepo/v1', 'packrepo'] for that key, [] for a key at
            the root of the workspace
    """
    parts = relative.split('/')
    return ['/'.join(parts[:index]) for index in range(len(parts) - 1, 0, -1)]


def require_valid(config):
    """
    Refuse a config file that the reader could not validate.

    YamlConfig falls back to the default of a field that fails the validation,
    which is what the generator can live with (a broken Lookback value still
    generates the packs it can) but not what the publish flow can: a typo in
    Publish would drop a remote, publish nowhere, and report success. The errors
    of the file are read here, so the flow refuses it.

    Args:
        config (YamlConfig): The config of a repo, its data is read here

    Raises:
        ConfigError: If any value of the file failed the validation
    """
    # the errors are filled by the read of the data, see YamlConfig.data
    _ = config.data
    errors = [error for error in config.errors if not isinstance(error, FileNotFoundError)]
    if errors:
        reasons = '; '.join(str(error) for error in errors)
        raise ConfigError(f'Cannot read "{config.file}": {reasons}')


def first_failure(group):
    """
    The failure a nursery of concurrent requests raised, unwrapped.

    A nursery collects the failures of its children into a group. The uploader
    runs several requests at once, so a run that fails reports the first failure
    as it is: a caller that knows what a failed request means is not helped by a
    group of one or two identical errors.

    Args:
        group (BaseExceptionGroup): The group a nursery raised

    Returns:
        BaseException: The first leaf of the group
    """
    for error in group.exceptions:
        if isinstance(error, BaseExceptionGroup):
            return first_failure(error)
        return error
    return group


def find_skip(group):
    """
    The first SkipChannel a nursery raised, if any.

    A gatherer that finds a broken generation cancels its sibling: the group may
    hold only that skip, and the caller turns it into a skipped channel instead
    of a failed one.

    Args:
        group (BaseExceptionGroup): The group a nursery raised

    Returns:
        SkipChannel: The first skip, None when the group holds another failure
    """
    for error in group.exceptions:
        if isinstance(error, SkipChannel):
            return error
        if isinstance(error, BaseExceptionGroup):
            found = find_skip(error)
            if found is not None:
                return found
    return None
