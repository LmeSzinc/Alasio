"""
What a mirror no longer serves: the keys a run may delete, and the decision that
picks them.

The decision is one function over data the caller already has -- the published
files, the files the mirror served before this run, the listing of the mirror
and the grace -- so it calls no store and can be tested without one. Its caller
is `ChannelUpload`, which retires right after it published, from the listing it
took before the writes.

The safety argument: a key is deleted only when neither contract the run knows
serves it -- the one it publishes, and the one the mirror served when the run
started. Every other key belongs to an older generation, and every older
generation stopped being the current one no later than the moment the last
contract landed. That moment is the clock of the retirement:

    T = last_modified of the contract in the listing

A generation is current from the moment the contract naming it lands until the
next contract lands, so at time T the generation the mirror was serving stopped
being served, and the one it replaced even earlier. `now - T >= grace` therefore
means that every generation older than the served one has been out of service
for the grace -- long enough for a client that read its barrier to finish the
update -- and their keys may go. While `now - T < grace` the generation this run
replaces is still inside its window, and nothing is deleted at all.

Why the contract and not the age of a key. The age of a key says when it was
written, never when it stopped being served: the packs of a generation land
before the barrier that makes it current, so a generation that served for a
month holds keys a month old while every client still runs it. A rule keyed on
that age deletes the generation that served longest first, which is exactly the
one most likely to be read at the moment of the switch. The contract is the one
object whose age names a switch, and it is one object rather than one per key: a
store does not report the time of an object for free on every driver, and a
decision that needed it would cost one request per key.

Why the contract of the mirror and not the one of the run. A first publication
has no contract to read and nothing to retire. A mirror that holds a barrier no
contract names -- what a run leaves behind when it switches the barrier and
fails before it writes the contract -- serves a generation whose file list
nobody knows; it is left alone, and the next run, which finds the two consistent
again, retires what that one replaced.

What this costs. A channel that publishes faster than the grace keeps every
generation of the last `grace` seconds: each run replaces a generation that is
still inside the window of the one before it, so none of them may go yet. That
is the intended shape -- a burst of releases accumulates, and the first run
after the burst, or after the pace drops below the grace, retires all of them in
one round. The bound of that accumulation is the pace of publishing, not the
tool: bounding it here would need the moment each generation was replaced, which
the mirror does not record except in this one object.
"""

from typing import List

import msgspec

from alasio.deploy_dev.pack_server.publish.common import BARRIER_KEY, MANIFEST_KEY, ancestor_folders


class SkipRetire(Exception):
    """
    Raised when a mirror does not hold the pair of records a run may act on.

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
        within_grace (bool): Whether the contract of the mirror is younger than
            the grace, so the plan is cut short by it
    """

    keys: List[str] = msgspec.field(default_factory=list)
    folders: List[str] = msgspec.field(default_factory=list)
    size: int = 0
    within_grace: bool = False


def build_delete_plan(
        files, previous_files, listing, grace, now,
        capabilities_dirs=False,
):
    """
    Decide what a retire round removes.

    Args:
        files (Iterable[str]): The published files of the generation this run
            publishes
        previous_files (Iterable[str]): The published files of the contract the
            mirror served when the run started, None when the run could not read
            it: nothing may be deleted then, because the generation being
            replaced is not known
        listing (ObjectListing): The mirror, keys relative to the workspace, as
            it was before the writes of this run
        grace (int): Seconds a generation must have stopped being the current one
            before its keys are deleted
        now (float): The clock of the server, `listing.server_date`
        capabilities_dirs (bool): True when the store has collections to remove.
            Defaults to False

    Returns:
        DeletePlan: The keys to delete, the collections to remove and the bytes
            they hold

    Raises:
        SkipRetire: If the listing does not hold both records of a generation,
            or holds them in the state of a switch that never completed: the
            workspace was wiped, or the file list of the generation being served
            is unknown
    """
    barrier = listing.objects.get(BARRIER_KEY)
    if barrier is None:
        raise SkipRetire(f'{BARRIER_KEY} is not in the listing of the mirror')
    contract = listing.objects.get(MANIFEST_KEY)
    if contract is None:
        raise SkipRetire(f'{MANIFEST_KEY} is not in the listing of the mirror')
    if previous_files is None:
        raise SkipRetire(f'{MANIFEST_KEY} of the mirror cannot be read')
    if barrier.last_modified > contract.last_modified:
        # the barrier was written after the contract next to it, which the flow
        # never does on purpose: a run switched the barrier and failed before it
        # wrote the contract, so the generation the clients read is one whose
        # file list nobody has. Nothing may go until a run publishes the two
        # together again
        raise SkipRetire(
            f'{BARRIER_KEY} of the mirror was written after {MANIFEST_KEY}, so the '
            f'generation being served is not the one the contract names')

    keep = set(files) | set(previous_files) | {MANIFEST_KEY}
    # the generation the mirror served stopped being current when the contract
    # beside it landed, and every generation older than that one stopped earlier
    within_grace = now - contract.last_modified < grace

    plan = DeletePlan(within_grace=within_grace)
    if within_grace:
        return plan

    plan.keys = sorted(
        relative for relative in listing.objects if relative not in keep)
    plan.size = sum(listing.objects[relative].size for relative in plan.keys)

    if capabilities_dirs:
        # a collection that holds a kept file is not a candidate, and a
        # collection that holds a key which survives this round is not either
        kept = {folder for relative in keep for folder in ancestor_folders(relative)}
        candidates = {
            folder for relative in plan.keys for folder in ancestor_folders(relative)
        } - kept
        plan.folders = sorted(candidates, key=lambda folder: (-folder.count('/'), folder))
    return plan
