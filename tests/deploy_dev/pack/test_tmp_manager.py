"""
Tests for TmpManager: the tmp file names of a deploy job workspace.

The manager scans the workspace at init (the leftovers of an interrupted
run are pooled per content, entries that are not tmp files of a job are
ignored and the ranks of a content may be non-continuous) and hands the
names out on demand: {size}_{sha1hex}_{rank}.tmp, rank being the
occurrence index of the content among the names of the run. The tmp
files a run writes are registered per content, so a reader finds a
content without walking the records of the run.
"""
import os
from hashlib import sha1
from threading import Lock, Thread
from time import sleep

from alasio.deploy.pack.tmp_manager import TmpManager as _TmpManager


class TmpManager(_TmpManager):
    """
    TmpManager scanned at construction, the reader of the tests.

    The manager itself only stores the workspace, its caller scans: the io
    of the scan runs wherever the caller runs it (JobBase.tmp_manager_init
    runs it in a worker thread). The tests build a manager right before
    reading it, so they scan here.
    """

    def __init__(self, workspace):
        super().__init__(workspace)
        self.scan()


from alasio.ext import env
from alasio.testing.filesystem import fs  # noqa: F401


def workspace():
    """
    Returns:
        PathStr: The workspace folder of the default target
    """
    return env.PROJECT_ROOT / '.pack/workspace'


class TestAtomicLeftovers:
    """The scan removes the leftovers of interrupted atomic writes."""

    def test_scan_removes_atomic_write_leftovers(self, fs):
        """A filename.RID.tmp left by an interrupted atomic write is removed
        by the scan, new_index.tmp and the tmp files of jobs stay."""
        root = '/atomic_leftovers'
        fs.create_file(f'{root}/job.pack.sTD2kF.tmp', contents='half written')
        fs.create_file(f'{root}/new_index.tmp', contents='index pack')
        fs.create_file(f'{root}/3_{"a" * 40}_0.tmp', contents='abc')

        manager = TmpManager(root)

        assert not os.path.exists(f'{root}/job.pack.sTD2kF.tmp')
        assert os.path.exists(f'{root}/new_index.tmp')
        assert manager.leftover == {(3, 'a' * 40): {0}}


def digest_of(content):
    """
    Args:
        content (bytes): File content

    Returns:
        bytes: Sha1 digest of the content
    """
    return sha1(content).digest()


def write_file(path, content=b''):
    """
    Write a file in the fake filesystem, the folders are created.

    Args:
        path (str): File path
        content (bytes): File content. Defaults to b''
    """
    os.makedirs(str(path).rpartition('/')[0], exist_ok=True)
    with open(path, 'wb') as f:
        f.write(content)


class YieldingDict(dict):
    """
    A dict that gives the GIL up before every write.

    A thread switch in the middle of the counter update of get() (between
    the read and the write) is what the lock of the manager guards
    against: with a plain dict the switch has to hit a tiny window, this
    dict opens it, so the test below fails on a manager without the lock.
    """

    def __setitem__(self, key, value):
        sleep(0.0005)
        super().__setitem__(key, value)


class TestGet:
    """get(): the tmp file names of the records, handed out on demand."""

    def test_first_rank(self, app_folder):
        """The first application for a content gets rank 0."""
        digest = digest_of(b'content')
        manager = TmpManager(workspace())
        assert manager.get(7, digest) == workspace().joinpath(f'7_{digest.hex()}_0.tmp')

    def test_rank_counts_the_same_content(self, app_folder):
        """Every application for the same content gets the next rank."""
        digest = digest_of(b'content')
        manager = TmpManager(workspace())
        assert [manager.get(7, digest) for _ in range(3)] == [
            workspace().joinpath(f'7_{digest.hex()}_{rank}.tmp')
            for rank in range(3)
        ]

    def test_different_contents_have_their_own_rank(self, app_folder):
        """The ranks of a content are independent of the other contents,
        even when their size is the same."""
        one = digest_of(b'one')
        two = digest_of(b'two')
        manager = TmpManager(workspace())
        assert manager.get(3, one) == workspace().joinpath(f'3_{one.hex()}_0.tmp')
        assert manager.get(3, two) == workspace().joinpath(f'3_{two.hex()}_0.tmp')
        assert manager.get(3, one) == workspace().joinpath(f'3_{one.hex()}_1.tmp')

    def test_empty_content(self, app_folder):
        """An empty file (size 0, sha1 b'') is named like any content."""
        manager = TmpManager(workspace())
        assert manager.get(0, b'') == workspace().joinpath('0__0.tmp')

    def test_leftovers_first_then_fresh(self, app_folder):
        """The leftovers of a content are handed out first, the lowest
        rank first; the fresh names then continue behind them."""
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_1.tmp'), b'content')
        manager = TmpManager(workspace())
        # the leftover
        assert manager.get(7, digest) == workspace().joinpath(f'7_{digest.hex()}_1.tmp')
        # the leftover is claimed: the fresh names continue behind it
        assert manager.get(7, digest) == workspace().joinpath(f'7_{digest.hex()}_2.tmp')
        assert manager.get(7, digest) == workspace().joinpath(f'7_{digest.hex()}_3.tmp')

    def test_leftovers_are_consumed(self, app_folder):
        """Every leftover is handed out once."""
        digest = digest_of(b'content')
        for rank in (2, 5):
            write_file(workspace().joinpath(f'7_{digest.hex()}_{rank}.tmp'), b'content')
        manager = TmpManager(workspace())
        assert manager.get(7, digest) == workspace().joinpath(f'7_{digest.hex()}_2.tmp')
        assert manager.get(7, digest) == workspace().joinpath(f'7_{digest.hex()}_5.tmp')
        assert manager.get(7, digest) == workspace().joinpath(f'7_{digest.hex()}_6.tmp')

    def test_claim_takes_the_picked_leftover(self, app_folder):
        """The rank a reader picked (see candidates()) is claimed with
        claim(): it leaves the pool and is not handed out again."""
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_3.tmp'), b'content')
        manager = TmpManager(workspace())
        rank, tmp = manager.candidates(7, digest)[0]
        assert rank == 3
        assert manager.claim(7, digest, rank) is True
        assert manager.candidates(7, digest) == []
        # the fresh names continue behind the claimed rank
        assert manager.get(7, digest) == workspace().joinpath(f'7_{digest.hex()}_4.tmp')

    def test_claim_of_a_gone_leftover(self, app_folder):
        """claim() reports a leftover that was handed out already, the
        caller falls back to a fresh name."""
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_0.tmp'), b'content')
        manager = TmpManager(workspace())
        assert manager.get(7, digest).endswith('_0.tmp')
        assert manager.claim(7, digest, 0) is False
        assert manager.claim(7, digest, 9) is False


class TestScan:
    """scan(): the index of the tmp files left in the workspace."""

    def test_leftovers_indexed(self, app_folder):
        """The tmp files of an interrupted run are pooled per content."""
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_0.tmp'), b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_1.tmp'), b'content')
        manager = TmpManager(workspace())
        assert manager.candidates(7, digest) == [
            (0, workspace().joinpath(f'7_{digest.hex()}_0.tmp')),
            (1, workspace().joinpath(f'7_{digest.hex()}_1.tmp')),
        ]

    def test_non_continuous_ranks(self, app_folder):
        """The ranks of a content may be non-continuous (the tmp file of a
        record was already moved to its target, or was never written):
        every file is indexed on its own, nothing assumes the ranks are
        dense."""
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_0.tmp'), b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_2.tmp'), b'content')
        manager = TmpManager(workspace())
        assert [rank for rank, _ in manager.candidates(7, digest)] == [0, 2]

    def test_invalid_names_ignored(self, app_folder):
        """Entries that do not follow the naming rule are not tmp files of
        a job and are ignored."""
        for name in (
            # the job file and the new index pack of a job
            'job.pack',
            'new_index.tmp',
            # not a sha1, or not a tmp file at all
            '7_zz_0.tmp',
            '7_garbage_0.tmp',
            '7_abc_0.tmp',
            'ab_0123456789abcdef0123456789abcdef01234567_0.tmp',
            '7_0123456789abcdef0123456789abcdef01234567_0.tmp.bak',
            # the tmp file of another mechanism (a random id)
            '7_0123456789abcdef0123456789abcdef01234567_0.a1B2c3.tmp',
        ):
            write_file(workspace().joinpath(name), b'?')
        # the valid tmp file of a job is indexed
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_0.tmp'), b'content')
        manager = TmpManager(workspace())
        assert [rank for rank, _ in manager.candidates(7, digest)] == [0]
        assert manager.candidates(7, digest_of(b'other')) == []

    def test_leftovers_of_the_position_scheme(self, app_folder):
        """The previous naming scheme ({size}_{sha1hex}_record position) is
        structurally the same name: its leftovers are pooled too, they
        are simply a cache entry of the same content."""
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_15.tmp'), b'content')
        manager = TmpManager(workspace())
        assert [rank for rank, _ in manager.candidates(7, digest)] == [15]

    def test_scan_keeps_the_counters(self, app_folder):
        """A rescan of the workspace does not restart the ranks: the names
        already handed out stay the names of their records."""
        digest = digest_of(b'content')
        manager = TmpManager(workspace())
        first = manager.get(7, digest)
        manager.scan()
        assert manager.get(7, digest) != first
        assert manager.get(7, digest).endswith('_2.tmp')

    def test_scan_keeps_a_claimed_leftover_claimed(self, app_folder):
        """A leftover that was handed out is not handed out again by a
        rescan (its file is still there until the flow moves it)."""
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_0.tmp'), b'content')
        manager = TmpManager(workspace())
        assert manager.get(7, digest).endswith('_0.tmp')
        manager.scan()
        assert manager.get(7, digest).endswith('_1.tmp')

    def test_missing_workspace(self, app_folder):
        """A fresh ledger has no workspace folder: no leftovers, and the
        names can still be handed out."""
        manager = TmpManager(workspace())
        assert manager.candidates(7, digest_of(b'x')) == []
        assert manager.get(5, digest_of(b'x')).endswith('_0.tmp')


class TestReuse:
    """The name is the key of the tmp file cache: the leftovers of an
    interrupted run are found again by the next run, whichever pack and
    record set wrote them."""

    def test_leftover_name_is_handed_out_again(self, app_folder):
        """A run that writes the tmps of a record set hands the same names
        out again: the caller finds the files and reuses them."""
        digest = digest_of(b'content')
        written = [TmpManager(workspace()).get(7, digest)]
        write_file(written[0], b'content')
        assert [TmpManager(workspace()).get(7, digest)] == written
        assert os.path.exists(written[0])

    def test_ranks_align_when_records_are_inserted(self, app_folder):
        """A new record of a content whose leftovers are all claimed gets
        a fresh name, the leftovers are not touched again."""
        digest = digest_of(b'content')
        run1 = TmpManager(workspace())
        written = [run1.get(7, digest) for _ in range(2)]
        for tmp in written:
            write_file(tmp, b'content')
        run2 = TmpManager(workspace())
        assert [run2.get(7, digest) for _ in range(3)] == written + [
            workspace().joinpath(f'7_{digest.hex()}_2.tmp')
        ]


class TestCandidates:
    """candidates(): the leftovers a reader may probe, nothing claimed."""

    def test_rank_order(self, app_folder):
        """The leftovers of a content, in rank order."""
        digest = digest_of(b'content')
        for rank in (5, 2):
            write_file(workspace().joinpath(f'7_{digest.hex()}_{rank}.tmp'), b'content')
        manager = TmpManager(workspace())
        assert [rank for rank, _ in manager.candidates(7, digest)] == [2, 5]

    def test_reading_claims_nothing(self, app_folder):
        """candidates() does not consume a leftover: get() still hands the
        same file out."""
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_0.tmp'), b'content')
        manager = TmpManager(workspace())
        for _ in range(3):
            assert manager.candidates(7, digest) == [
                (0, workspace().joinpath(f'7_{digest.hex()}_0.tmp')),
            ]
        assert manager.get(7, digest).endswith('_0.tmp')


class TestRegister:
    """register(): the tmp files this run wrote, for the readers."""

    def test_registered_file_is_a_candidate(self, app_folder):
        """A registered tmp file is a candidate of its content, after the
        leftovers of the content."""
        digest = digest_of(b'content')
        write_file(workspace().joinpath(f'7_{digest.hex()}_0.tmp'), b'content')
        manager = TmpManager(workspace())
        assert manager.candidates(7, digest) == [
            (0, workspace().joinpath(f'7_{digest.hex()}_0.tmp')),
        ]
        written = workspace().joinpath(f'7_{digest.hex()}_4.tmp')
        manager.register(7, digest, written)
        assert manager.candidates(7, digest) == [
            (0, workspace().joinpath(f'7_{digest.hex()}_0.tmp')),
            (-1, written),
        ]

    def test_registered_file_is_read_only(self, app_folder):
        """A tmp file of this run belongs to its record: a reader only
        reads it (rank -1), and get() never hands it out again."""
        digest = digest_of(b'content')
        manager = TmpManager(workspace())
        tmp = manager.get(7, digest)
        manager.register(7, digest, tmp)
        assert manager.candidates(7, digest) == [(-1, tmp)]
        assert manager.claim(7, digest, 0) is False
        assert manager.get(7, digest) == workspace().joinpath(f'7_{digest.hex()}_1.tmp')

    def test_rescan_keeps_the_registry(self, app_folder):
        """A rescan keeps the registry: the files this run wrote stay
        readable for the readers, and they never enter the pool."""
        digest = digest_of(b'content')
        manager = TmpManager(workspace())
        tmp = manager.get(7, digest)
        write_file(tmp, b'content')
        manager.register(7, digest, tmp)
        manager.scan()
        assert manager.candidates(7, digest) == [(-1, tmp)]
        assert manager.get(7, digest).endswith('_1.tmp')


class TestThreadSafety:
    """The manager is shared by the threads of a run: the applications
    for a name are atomic."""

    def test_concurrent_get_hands_out_unique_ranks(self, app_folder):
        """Concurrent applications for the same content never share a
        name: the ranks of a content are handed out under the lock."""
        digest = digest_of(b'content')
        manager = TmpManager(workspace())
        # a thread switch in the middle of the counter update of get()
        manager.next = YieldingDict(manager.next)
        names = []
        collect = Lock()

        def worker():
            local = [manager.get(7, digest) for _ in range(50)]
            with collect:
                names.extend(local)

        threads = [Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(names) == 400
        assert len(set(names)) == 400
        ranks = {int(str(name).rpartition('_')[2][:-4]) for name in names}
        assert ranks == set(range(400))

    def test_concurrent_get_of_different_contents(self, app_folder):
        """The counters of different contents are independent, even when
        their size is the same."""
        contents = [b'content-a', b'content-b', b'content-c']
        manager = TmpManager(workspace())
        names = []
        collect = Lock()

        def worker(content):
            digest = digest_of(content)
            local = [manager.get(len(content), digest) for _ in range(50)]
            with collect:
                names.extend(local)

        threads = [Thread(target=worker, args=(content,)) for content in contents]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(set(names)) == 150
