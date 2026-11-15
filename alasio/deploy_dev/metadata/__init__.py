"""
Read the metadata of a distribution version, once, for the developer tools.

The package is the developer side of the index: the pack_deps manager decides
the pack channel of a mod from the wheels a pinned version publishes, and the
compiler resolves a lock from the ``Requires-Dist`` of the versions it picks.
Both ask the same question of the same index, so they share one reader here,
which keeps the pages and the METADATA of a run in memory and reads them once
(see :mod:`alasio.deploy_dev.metadata.provider`).

The client runtime does not use it: a client installs with pip, and reads the
METADATA of what it installed from the disk (alasio.deploy.simple_pip.pip_list),
so nothing of this package ships in the dependency step of a mod.

Usage:
    from alasio.deploy_dev.metadata import MetadataProvider

    provider = MetadataProvider(client)
    meta = await provider.metadata('httpx2', '2.12.0')
    await provider.aclose()
"""

from alasio.deploy_dev.metadata.provider import (
    DEFAULT_CONCURRENCY, DEFAULT_INDEX, MODE_FULL, MODE_PEP658, MODE_RANGE, MODE_RANGE_NO_SUFFIX, ClosureResult,
    ClosureRoot, DistributionMetadata, MetadataError, MetadataProvider, SourceCapabilities, readable_wheels
)
from alasio.deploy_dev.metadata.requirements import (
    PLATFORMS, marker_environments, requirement_names, requires_dist, wheel_metadata
)

__all__ = [
    'DEFAULT_CONCURRENCY',
    'DEFAULT_INDEX',
    'MODE_FULL',
    'MODE_PEP658',
    'MODE_RANGE',
    'MODE_RANGE_NO_SUFFIX',
    'PLATFORMS',
    'ClosureResult',
    'ClosureRoot',
    'DistributionMetadata',
    'MetadataError',
    'MetadataProvider',
    'SourceCapabilities',
    'marker_environments',
    'readable_wheels',
    'requirement_names',
    'requires_dist',
    'wheel_metadata',
]
