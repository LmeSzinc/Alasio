"""
What a mirror no longer serves: the keys a run may delete, and the decision that
picks them.

The decision is one function over data the caller already has -- the published
files, the listing of the mirror, the grace and the retention -- so it calls no
store and can be tested without one. Its caller is `ChannelUpload`, which
retires the leftovers of the generation it replaced right after it published
the new one, from the listing it took before the writes.

The safety argument of the decision: a key is deleted only when the manifest
does not list it AND the generation it belongs to stopped being the current one
long enough ago that every client that read its barrier has had the grace to
finish its update. The moment that generation stopped being current is the
moment the barrier replacing it was written:

- the run publishes a new barrier (the version changed): the keys the manifest
  no longer lists stopped being current with this run, so the grace starts now
  and nothing is deleted this round -- a client may have read the old barrier a
  moment ago;
- the run publishes nothing (the version did not change): the barrier of the
  listing is the current one, and those keys stopped being current when it was
  written.

The first condition keeps everything the generation serves. The second keeps a
generation a client may still be reading, and it uses the barrier rather than
the key itself because a key is written before the barrier that makes it
current: a key's own age says nothing about when it stopped being served.

A key older than the retention is deleted whatever the barrier says. A channel
whose content changes on every run rewrites its barrier on every run, so the
grace alone would never let any generation go; the retention is the upper bound
of that wait, and it has to stay far above the time a client needs to download
one generation.

The two numbers answer two different questions and neither implies the other:
the grace asks "may a client still be reading this?", the retention asks "is
this old enough that keeping it is waste?". Under a channel that changes on
every run the grace is always in force, so it can never bound the disk on its
own; under a channel that rarely changes the retention never fires. The
retention is a constant of the tool rather than a setting of the remote: it
bounds the disk, which is the tool's business, like the concurrency cap.
"""

from typing import List

import msgspec

from alasio.deploy_dev.pack_server.publish.common import BARRIER_KEY, MANIFEST_KEY, ancestor_folders

# How long a key of a retired generation is kept when the generations keep
# replacing each other faster than the grace allows to retire them. The grace
# alone bounds nothing on such a channel: every run rewrites the barrier, so
# every run finds the replaced generation still inside its window. A day is far
# past any client's update (the check interval of a backend is minutes) and
# caps a channel that publishes every half hour at about 48 generations, i.e.
# roughly 3.7 GB of a 78 MB generation. Raised to the grace when the grace of a
# remote is larger than it.
MAX_RETENTION = 86400


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
        within_grace (bool): Whether the generation being replaced has served
            less than the grace, so the plan is cut short by it
    """

    keys: List[str] = msgspec.field(default_factory=list)
    folders: List[str] = msgspec.field(default_factory=list)
    size: int = 0
    within_grace: bool = False


def build_delete_plan(
        files, listing, grace, now, write_barrier,
        max_retention=MAX_RETENTION, capabilities_dirs=False,
):
    """
    Decide what a retire round removes.

    Args:
        files (Iterable[str]): The published files of the manifest
        listing (ObjectListing): The mirror, keys relative to the workspace
        grace (int): Seconds a generation must have stopped being the current
            one before its keys are deleted
        now (float): The clock of the server, `listing.server_date`
        write_barrier (bool): Whether this run publishes a new barrier. True
            when the version changed: the keys the manifest no longer lists
            stopped being current with this run
        max_retention (int): Seconds after which a key is deleted whatever the
            barrier says, the upper bound of the wait. Defaults to
            MAX_RETENTION
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

    # The moment the keys the manifest no longer lists stopped being current.
    # A run that writes the barrier replaces the generation now, so its keys
    # stop being current now and the whole grace still belongs to the clients
    # that read it. A run that writes nothing finds the current barrier in the
    # listing: those keys stopped being current when it was written, and a
    # client that read it has had the grace to finish since then.
    expired_at = now if write_barrier else barrier.last_modified
    within_grace = now - expired_at < grace

    plan = DeletePlan(within_grace=within_grace)
    for relative, info in listing.objects.items():
        if relative in keep:
            continue
        if within_grace and now - info.last_modified < max_retention:
            # the replaced generation is still inside its grace, and the key is
            # not old enough for the retention to override it
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
