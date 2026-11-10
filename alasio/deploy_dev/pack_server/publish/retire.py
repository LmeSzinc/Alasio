"""
What a mirror no longer serves: the keys a run may delete, and the decision that
picks them.

The decision is one function over data the caller already has -- the published
files, the listing of the mirror and the grace -- so it calls no store and can
be tested without one. Its caller is `ChannelUpload`, which retires the
leftovers of the generation it replaced right after it published the new one,
from the listing it took before the writes.

The safety argument of the decision: a key is deleted only when the manifest
does not list it AND the barrier of the mirror has been in place for the grace
AND the key itself is older than the grace. The first condition keeps everything
the generation serves; the other two keep a generation the mirror has only
recently started serving, and the leftovers of an interrupted upload.
"""

from typing import List

import msgspec

from alasio.deploy_dev.pack_server.publish.common import BARRIER_KEY, MANIFEST_KEY, ancestor_folders


class SkipRetire(Exception):
    """
    Raised when a mirror does not hold a generation a run may act on.

    A skip never deletes: the mirror keeps whatever it has, and the next run
    looks again. The message is the reason and is logged once.
    """


class DeletePlan(msgspec.Struct):
    """
    What one retire round would remove, and the collections it would leave empty.

    Attributes:
        keys (List[str]): Keys to delete, sorted
        folders (List[str]): Collections that hold none of the kept keys, deepest
            first; empty for a store without collections
        size (int): Bytes the deleted keys hold
    """

    keys: List[str] = msgspec.field(default_factory=list)
    folders: List[str] = msgspec.field(default_factory=list)
    size: int = 0


def build_delete_plan(files, listing, grace, now, capabilities_dirs=False):
    """
    Decide what a retire round removes.

    Args:
        files (Iterable[str]): The published files of the manifest
        listing (ObjectListing): The mirror, keys relative to the workspace
        grace (int): Seconds the barrier must have been in place before a key is
            deleted, and seconds a key must be older than as well
        now (float): The clock of the server, `listing.server_date`
        capabilities_dirs (bool): True when the store has collections to remove.
            Defaults to False

    Returns:
        DeletePlan: The keys to delete, the collections to remove and the bytes
            they hold

    Raises:
        SkipRetire: If the listing does not hold the barrier of the generation:
            the workspace was wiped or changed between the two reads
    """
    keep = set(files) | {MANIFEST_KEY}
    barrier = listing.objects.get(BARRIER_KEY)
    if barrier is None:
        raise SkipRetire(f'{BARRIER_KEY} is not in the listing of the mirror')

    plan = DeletePlan()
    for relative, info in listing.objects.items():
        if relative in keep:
            continue
        if now - info.last_modified <= grace:
            # the file is younger than the grace: a leftover of an interrupted
            # upload, or a generation that is still being written
            continue
        plan.keys.append(relative)
        plan.size += info.size
    plan.keys.sort()

    if capabilities_dirs:
        # a collection that holds a kept file is not a candidate, and a
        # collection that holds a key which survives this round is not either
        kept = {folder for relative in keep for folder in ancestor_folders(relative)}
        candidates = {
            folder for relative in plan.keys for folder in ancestor_folders(relative)
        } - kept
        plan.folders = sorted(candidates, key=lambda folder: (-folder.count('/'), folder))
    return plan
