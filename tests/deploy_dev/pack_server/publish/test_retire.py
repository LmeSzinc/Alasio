"""
The retire decision on a mirror the tests build by hand.

The function is pure -- the two contracts, the listing and the grace -- so the
mirror of a test is the whole input and the plan is compared whole. The
publication that calls it is tested against the fake service (test_upload.py),
which is where the order of a run and the requests it costs are asserted.
"""

import pytest

from alasio.deploy_dev.pack_server.publish.common import BARRIER_KEY, MANIFEST_KEY
from alasio.deploy_dev.pack_server.publish.retire import DeletePlan, SkipRetire, build_delete_plan
from alasio.deploy_dev.store import ObjectInfo, ObjectListing

# the clock of the tests: any fixed second works, the decision only ever looks at
# differences
NOW = 1_600_000_000.0
GRACE = 3600


def info(size, time):
    """
    What a mirror reports about one object.

    Args:
        size (int): Size in bytes
        time (float): Last modified, on the clock of the server

    Returns:
        ObjectInfo: The state of the object
    """
    return ObjectInfo(size=size, etag='', last_modified=time)


def files_of(version):
    """
    The published files of a generation: its packs, the barrier, nothing else.

    Args:
        version (str): Commit of the generation

    Returns:
        list[str]: The keys of its contract
    """
    return [
        f'packrepo/{version}/full.pack',
        f'packrepo/{version}/update_00000001.pack',
        BARRIER_KEY,
    ]


def mirror(served, older=(), contract_age=0.0, extra=None):
    """
    A mirror that serves `served`, with `older` generations behind it.

    Every generation lands a grace before the next one does, so the ages of the
    packs follow the order of the versions, and the contract and the barrier of
    the served one share the moment it landed.

    Args:
        served (str): Commit of the generation the contract of the mirror names
        older (Iterable[str]): Commits of the generations before it, oldest first
        contract_age (float): Seconds since the contract of `served` landed
        extra (dict): Further keys of the mirror, key -> ObjectInfo

    Returns:
        ObjectListing: The mirror
    """
    objects = dict(extra or {})
    for index, version in enumerate(older):
        age = contract_age + (len(older) - index) * GRACE
        objects[f'packrepo/{version}/full.pack'] = info(100, NOW - age)
        objects[f'packrepo/{version}/update_00000001.pack'] = info(11, NOW - age)
    objects[f'packrepo/{served}/full.pack'] = info(100, NOW - contract_age)
    objects[f'packrepo/{served}/update_00000001.pack'] = info(11, NOW - contract_age)
    objects[BARRIER_KEY] = info(60, NOW - contract_age)
    objects[MANIFEST_KEY] = info(80, NOW - contract_age)
    return ObjectListing(objects=objects, server_date=NOW)


def plan_of(files, previous_files, listing, grace=GRACE):
    """
    The plan of one round, on the clock and the mirror of the tests.

    Returns:
        DeletePlan: What the round would remove
    """
    return build_delete_plan(files, previous_files, listing, grace, NOW)


class TestSkip:
    """The states of a mirror no run may delete from."""

    def test_a_mirror_without_a_barrier(self):
        """Without the barrier the mirror is not one this run recognises."""
        objects = {
            'packrepo/aaaaaaaa/full.pack': info(100, NOW - GRACE * 2),
            MANIFEST_KEY: info(80, NOW - GRACE * 2),
        }
        listing = ObjectListing(objects=objects, server_date=NOW)
        with pytest.raises(SkipRetire, match=BARRIER_KEY):
            plan_of(files_of('bbbbbbbb'), files_of('aaaaaaaa'), listing)

    def test_a_mirror_without_a_contract(self):
        """A first publication has no generation to retire, and no clock."""
        objects = {
            'packrepo/aaaaaaaa/full.pack': info(100, NOW - GRACE * 2),
            BARRIER_KEY: info(60, NOW - GRACE * 2),
        }
        listing = ObjectListing(objects=objects, server_date=NOW)
        with pytest.raises(SkipRetire, match=MANIFEST_KEY):
            plan_of(files_of('bbbbbbbb'), None, listing)

    def test_a_contract_the_run_could_not_read(self):
        """A generation whose file list is unknown is not deleted from."""
        listing = mirror('aaaaaaaa', contract_age=GRACE * 2)
        with pytest.raises(SkipRetire, match=MANIFEST_KEY):
            plan_of(files_of('bbbbbbbb'), None, listing)

    def test_a_barrier_written_after_the_contract(self):
        """A switch that never published its contract leaves a generation unnamed.

        The flow writes the barrier before the contract, so a mirror whose
        barrier is newer is one whose clients read a generation the contract does
        not name: nothing may go until a run publishes the two together.
        """
        listing = mirror('aaaaaaaa', contract_age=GRACE * 2)
        listing.objects[BARRIER_KEY] = info(60, NOW - GRACE)
        with pytest.raises(SkipRetire, match='written after'):
            plan_of(files_of('bbbbbbbb'), files_of('aaaaaaaa'), listing)


class TestTheClock:
    """The contract of the mirror is the only clock of the retirement."""

    def test_a_young_contract_retires_nothing(self):
        """The generation this run replaces is still inside its window."""
        listing = mirror('aaaaaaaa', contract_age=GRACE - 1)
        plan = plan_of(files_of('bbbbbbbb'), files_of('aaaaaaaa'), listing)
        assert plan == DeletePlan(within_grace=True)

    def test_a_contract_at_the_grace_retires(self):
        """The window is over the moment the contract is exactly a grace old."""
        listing = mirror('aaaaaaaa', older=['00000000'], contract_age=GRACE)
        plan = plan_of(files_of('bbbbbbbb'), files_of('aaaaaaaa'), listing)
        assert plan.within_grace is False
        assert plan.keys == [
            'packrepo/00000000/full.pack',
            'packrepo/00000000/update_00000001.pack',
        ]

    def test_the_served_generation_is_kept_however_old_it_is(self):
        """A generation that served for a week is not retired by the switch.

        Every object of the mirror is a week old, because the packs land before
        the barrier that makes them current. The generation they belong to stops
        being served with this run, so it keeps the whole grace -- an age of a
        key never cuts that window short.
        """
        listing = mirror('aaaaaaaa', contract_age=86400 * 7)
        plan = plan_of(files_of('bbbbbbbb'), files_of('aaaaaaaa'), listing)
        assert plan.keys == []
        assert plan.size == 0
        assert plan.within_grace is False

    def test_the_generations_before_the_served_one_go(self):
        """Every generation the contract does not name is out of its window."""
        listing = mirror('bbbbbbbb', older=['aaaaaaaa'], contract_age=GRACE + 1)
        plan = plan_of(files_of('cccccccc'), files_of('bbbbbbbb'), listing)
        assert plan.keys == [
            'packrepo/aaaaaaaa/full.pack',
            'packrepo/aaaaaaaa/update_00000001.pack',
        ]
        assert plan.size == 111

    def test_a_rollback_keeps_the_generation_it_publishes(self):
        """A rollback publishes the contract of an older generation again.

        The keys of the generation the mirror served are kept because its own
        contract names them, so the rollback target and the generation it
        replaces both survive the round, whatever the version numbers say.
        """
        listing = mirror('bbbbbbbb', older=['aaaaaaaa'], contract_age=GRACE + 1)
        plan = plan_of(files_of('aaaaaaaa'), files_of('bbbbbbbb'), listing)
        assert plan.keys == []


class TestKeep:
    """The keys both contracts serve are never candidates."""

    def test_every_key_of_both_contracts_is_kept(self):
        """The union of the two contracts is the keep set."""
        listing = mirror('bbbbbbbb', older=['aaaaaaaa'], contract_age=GRACE + 1)
        plan = plan_of(files_of('cccccccc') + files_of('bbbbbbbb'),
                       files_of('bbbbbbbb') + files_of('aaaaaaaa'), listing)
        assert plan.keys == []

    def test_a_dropped_file_is_retired_like_a_pack(self):
        """A key neither contract names goes once the window is over."""
        wheel = 'wheel/msgspec/0.21.0/msgspec-0.21.0-py3-none-any.whl'
        listing = mirror('bbbbbbbb', contract_age=GRACE + 1, extra={
            wheel: info(300, NOW - GRACE * 5),
        })
        plan = plan_of(files_of('bbbbbbbb'), files_of('bbbbbbbb'), listing)
        assert plan.keys == [wheel]
        assert plan.size == 300

    def test_a_key_the_contract_the_run_replaces_names_is_kept(self):
        """A file dropped by this run stays while the served generation serves it."""
        wheel = 'wheel/msgspec/0.21.0/msgspec-0.21.0-py3-none-any.whl'
        listing = mirror('bbbbbbbb', contract_age=GRACE + 1, extra={
            wheel: info(300, NOW - GRACE * 5),
        })
        plan = plan_of(files_of('cccccccc'), files_of('bbbbbbbb') + [wheel], listing)
        assert plan.keys == []

    def test_the_contract_of_the_mirror_is_never_deleted(self):
        """manifest.json is the record of the next run."""
        listing = mirror('bbbbbbbb', contract_age=GRACE + 1)
        plan = plan_of([], [], listing)
        assert MANIFEST_KEY not in plan.keys

    def test_the_folders_of_the_deleted_keys_are_removed(self):
        """A collection goes only when nothing kept lives in it."""
        listing = mirror('bbbbbbbb', older=['aaaaaaaa'], contract_age=GRACE + 1, extra={
            'wheel/msgspec/0.21.0/msgspec-0.21.0-py3-none-any.whl': info(300, NOW - GRACE * 5),
            'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl': info(300, NOW - GRACE * 5),
        })
        plan = build_delete_plan(
            files_of('bbbbbbbb') + ['wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl'],
            files_of('bbbbbbbb'), listing, GRACE, NOW, capabilities_dirs=True)
        assert set(plan.folders) == {'packrepo/aaaaaaaa', 'wheel/msgspec/0.21.0'}

    def test_a_store_without_folders_has_no_folders_to_remove(self):
        """A flat store is told nothing about collections."""
        listing = mirror('bbbbbbbb', older=['aaaaaaaa'], contract_age=GRACE + 1)
        plan = plan_of(files_of('bbbbbbbb'), files_of('bbbbbbbb'), listing)
        assert plan.keys and plan.folders == []
