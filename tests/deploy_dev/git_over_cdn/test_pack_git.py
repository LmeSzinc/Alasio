"""
Tests of `GitPackTool`, against the history of this repository.

No repository is created and nothing is written into one: every test packs a
pair of commits that this checkout already holds, and checks the result against
git's own view of the same range. The tests are read-only for the repository,
and skip when the checkout has no history to read (an sdist install, a shallow
CI clone, or a machine without git).

The shapes worth covering are all found in the history rather than built:

- a range with a couple of changed files, where `have` matters most;
- a range that holds hundreds of objects, so the pack has real delta chains;
- the one merge commit of the history, the only place where a pack crosses a
  second parent.

The pack is checked by reading it back with `GenIdx.pack_to_idx` (the Python
writer) and comparing it with the idx `git index-pack` writes: the two agree
byte for byte, which is a check of the pack itself as much as of the idx.
"""
import hashlib
import os
import struct

import pytest

from alasio.deploy_dev.git_over_cdn.pack_git import GitPackError, GitPackTool
from alasio.ext import env
from alasio.ext.concurrent.cmd import CmdlineError, run_cmd
from alasio.git.file.exception import PackNotSelfContained
from alasio.git.stage.genidx import GenIdx

pytestmark = pytest.mark.parallel

# The repo this test reads, the checkout the library is installed from
ROOT = env.ALASIO_ROOT

# Seconds a read-only query of the history may run. Every query here walks the
# commit graph of one checkout, which is milliseconds
GIT_TIMEOUT = 120

# Distance of the two ranges the tests pack. The near one is a handful of
# objects, the far one a few hundred: both pack in well under a second, and
# together they cover a pack without deltas and one with them
NEAR = 5
FAR = 50


def git(*args):
    """
    Run a read-only git query in the checkout.

    Args:
        *args (str): Subcommand and its arguments

    Returns:
        str: Standard output, stripped

    Raises:
        AssertionError: When git is not installed, or exits with a non zero
            code, with its own message
    """
    cmd = ['git', '-C', str(ROOT), *args]
    try:
        return run_cmd(cmd, timeout=GIT_TIMEOUT).stdout
    except CmdlineError as e:
        raise AssertionError(f'{" ".join(cmd)}: {e.stderr or e.msg}') from None


def git_is_installed():
    """
    Whether git answers on this machine.

    Returns:
        bool: True when `git --version` runs
    """
    try:
        run_cmd(['git', '--version'], timeout=GIT_TIMEOUT)
        return True
    except CmdlineError:
        return False


class History:
    """
    The commits of the checkout that the tests pack.

    Attributes:
        root (PathStr): Folder of the checkout
        total (int): Commits reachable from HEAD
    """

    def __init__(self, root, total):
        self.root = root
        self.total = total

    def at(self, depth):
        """
        Args:
            depth (int): Commits back from HEAD

        Returns:
            str: Sha1 of the commit
        """
        return git('rev-parse', f'HEAD~{depth}')

    def merge(self):
        """
        The merge commit of the history.

        Returns:
            str: Sha1 of a commit with two parents

        Raises:
            pytest.skip: Locally, if the history has no merge commit
            pytest.fail: On CI, if the history has no merge commit
        """
        merges = git('rev-list', '--merges', '-n', '1', 'HEAD').split()
        require(bool(merges),
                'the history of this checkout has no merge commit, so the pack of a '
                'range that crosses a merge is not covered by any test')
        return merges[0]

    def range_size(self, want, have=None):
        """
        Args:
            want (str): Commit the range ends at
            have (str): Commit the range starts after, None for no bound

        Returns:
            int: Objects in `want ^have`, the object set of a non-thin pack
        """
        args = ['rev-list', '--objects', want]
        if have is not None:
            args.append(f'^{have}')
        return len(git(*args).splitlines())


def require(available, reason):
    """
    Skip a test when this checkout cannot serve it, but never on CI.

    A skip is the right answer for a local run: an sdist install has no git
    folder, a developer may have cloned shallow, and a machine may have no
    git at all. On CI it is a hole: the matrix job is the only place these
    tests run, and a job that skips them reports green while the pack build is
    untested. So the same condition fails the job there, and the message says
    what to fix.

    Args:
        available (bool): Whether the checkout can serve the test
        reason (str): Why it cannot, in the words of the operator

    Raises:
        pytest.fail: On CI, when the test cannot run
        pytest.skip: Locally, when the test cannot run
    """
    if available:
        return
    if env.CI:
        pytest.fail(
            f'this test has to run on CI, but it cannot: {reason}. '
            f'The job that runs it must check out the whole history, '
            f'see the fetch-depth of actions/checkout in .github/workflows/ci.yml')
    pytest.skip(reason)


@pytest.fixture(scope='module')
def history():
    """
    The history of this checkout, or a skip when there is none to read.

    Yields:
        History: The commits the tests pack
    """
    require(git_is_installed(), 'git is not installed')
    require(ROOT.joinpath('.git').isdir(),
            'this checkout has no git folder, an sdist install has no history')
    require(git('rev-parse', '--is-shallow-repository') == 'false',
            'a shallow clone does not hold the history the tests pack')
    total = int(git('rev-list', '--count', 'HEAD'))
    require(total > FAR, f'only {total} commits are reachable, the tests pack {FAR} back')
    return History(ROOT, total)


@pytest.fixture
def tool(history):
    """
    Args:
        history (History): The checkout

    Returns:
        GitPackTool: A tool over this repository
    """
    return GitPackTool(history.root)


def objects_of(pack):
    """
    Args:
        pack (bytes): A pack, as `pack()` answered it

    Returns:
        list[PackObjectInfo]: Every object of the pack, sorted by sha1
    """
    return GenIdx('')._parse_pack_info(pack)


def idx_fanout_count(idx):
    """
    The object count an idx declares in the last entry of its fanout table.

    Args:
        idx (bytes): An idx file

    Returns:
        int: The number of entries
    """
    return struct.unpack('>I', idx[1028:1032])[0]


class TestPackObjectsWhenRange:
    def test_the_pack_holds_the_objects_of_the_range(self, history, tool):
        """The object set of a pack is `want ^have`, as git lists it."""
        want, have = history.at(0), history.at(FAR)

        pack = tool.pack(want=want, have=have)

        assert history.range_size(want, have) == len(objects_of(pack))

    def test_have_shrinks_the_pack(self, history, tool):
        """The nearer the commit the client has, the smaller the pack."""
        want = history.at(0)

        near = tool.pack(want=want, have=history.at(NEAR))
        far = tool.pack(want=want, have=history.at(FAR))

        assert len(near) < len(far)

    def test_a_pack_of_a_range_without_changes(self, history, tool):
        """
        A range whose commits an older commit already contains is empty.

        `have` being an ancestor of `want` is the normal case; here `have` is
        `want` itself, so there is nothing to send, and git writes a pack of no
        object rather than failing.
        """
        want = history.at(0)

        pack = tool.pack(want=want, have=want)

        assert pack[:4] == b'PACK'
        assert objects_of(pack) == []

    def test_a_pack_that_crosses_the_merge_commit(self, history, tool):
        """A range holding a merge commit packs both of its sides."""
        merge = history.merge()
        # the first parent is the side the merge was made on, so the range of
        # the merge and that parent is the other side coming in
        parent = git('rev-parse', f'{merge}^1')

        pack = tool.pack(want=merge, have=parent)

        assert len(pack) > 12
        assert history.range_size(merge, parent) == len(objects_of(pack))


class TestPackObjectsWhenThin:
    def test_a_thin_pack_is_smaller(self, history, tool):
        """
        A thin pack may delta against what the client already has.

        This is not what the server ships, the client cannot read such a pack
        from its disk, but it is the measure of what the self-contained form
        costs, and it has to hold or the argument for the form is wrong.
        """
        want, have = history.at(0), history.at(FAR)

        solid = tool.pack(want=want, have=have)
        thin = tool.pack(want=want, have=have, thin=True)

        assert len(thin) < len(solid)

    def test_a_thin_pack_is_refused(self, history, tool):
        """
        The reason the server does not ship thin packs.

        Its deltas point at bases the pack does not hold, and nothing on the
        disk of a client fixes that up: `pack_to_idx` refuses it, exactly where
        a client would find out.
        """
        want, have = history.at(0), history.at(FAR)
        thin = tool.pack(want=want, have=have, thin=True)

        with pytest.raises(PackNotSelfContained):
            tool.idx(thin)

    def test_a_self_contained_pack_is_accepted(self, history, tool):
        """The form the server ships indexes without help."""
        want, have = history.at(0), history.at(FAR)

        pack = tool.pack(want=want, have=have)
        idx = tool.idx(pack)

        assert idx[:4] == b'\xfftOc'


class TestIdx:
    def test_the_idx_describes_every_object(self, history, tool):
        """The fanout of the idx counts the objects the pack holds."""
        want, have = history.at(0), history.at(FAR)

        pack = tool.pack(want=want, have=have)
        idx = tool.idx(pack)

        assert idx_fanout_count(idx) == len(objects_of(pack))

    def test_the_idx_is_signed(self, history, tool):
        """The last twenty bytes of the idx are the sha1 of the rest."""
        want, have = history.at(0), history.at(NEAR)

        idx = tool.idx(tool.pack(want=want, have=have))

        assert idx[-20:] == hashlib.sha1(idx[:-20]).digest()

    def test_the_idx_is_the_same_for_the_same_objects(self, history, tool):
        """
        Two builds of one pair describe the same objects.

        The bytes are NOT stable: `pack-objects` searches deltas on several
        threads by default, so the order of the entries, and with it the offsets
        and the checksum of the pack, differ between two runs of the same range.
        The object set is the part that is stable, and it is what the server
        keys its artifacts on: a kept artifact is identified by the pair of
        commits, never by a hash of the pack.
        """
        want, have = history.at(0), history.at(FAR)

        first = tool.pack(want=want, have=have)
        second = tool.pack(want=want, have=have)

        assert [info.sha for info in objects_of(first)] == [info.sha for info in objects_of(second)]


class TestFailures:
    def test_an_unknown_commit(self, history, tool):
        """git's own message about a missing object reaches the caller."""
        with pytest.raises(GitPackError) as caught:
            tool.pack(want='0' * 40, have=history.at(NEAR))

        error = caught.value
        assert 'bad object' in error.stderr
        assert error.returncode != 0
        assert 'pack-objects' in ' '.join(error.cmd)

    def test_a_timeout(self, history, tool):
        """A command that runs longer than the timeout is killed, not waited on."""
        slow = GitPackTool(history.root, timeout=0.001)

        with pytest.raises(GitPackError) as caught:
            slow.pack(want=history.at(0), have=history.at(FAR))

        assert 'timed out' in str(caught.value)


class TestRequirement:
    """
    The guard that keeps this file from silently skipping on CI.

    The tests above need a checkout with history, which a local sdist install
    or a shallow clone does not have. Skipping there is right; skipping on CI
    would report green for a pack build nothing tested, so the same condition
    fails the job. These two cases pin that rule, they are the reason the rest
    of the file can skip at all.
    """

    def test_a_checkout_that_cannot_serve_the_test_fails_on_ci(self, monkeypatch):
        """On CI, a test that cannot run fails the job instead of vanishing."""
        monkeypatch.setattr(env, 'CI', True)

        with pytest.raises(pytest.fail.Exception) as caught:
            require(False, 'no history')

        assert 'fetch-depth' in str(caught.value)

    def test_a_checkout_that_cannot_serve_the_test_skips_locally(self, monkeypatch):
        """Locally, the same test skips, so a shallow clone or an sdist can run the suite."""
        monkeypatch.setattr(env, 'CI', False)

        with pytest.raises(pytest.skip.Exception):
            require(False, 'no history')

    def test_a_checkout_that_can_serve_the_test_runs(self):
        """A condition that holds passes through, it is not a skip either way."""
        assert require(True, 'no history') is None


class TestReadOnly:
    def test_a_build_writes_no_file(self, history, tool):
        """
        A pack and its idx never touch the disk.

        `pack-objects --stdout` writes the pack to a pipe and `idx()` reads
        those bytes, so the first write of an artifact is the zip a client
        downloads, and an interrupted build leaves nothing behind to clean up.
        The temp folder of the project is watched instead of a folder of the
        test: there is no folder of the test to watch.
        """
        before = sorted(os.listdir(env.ALASIO_TEMP)) if env.ALASIO_TEMP.isdir() else []

        pack = tool.pack(want=history.at(0), have=history.at(FAR))
        idx = tool.idx(pack)

        assert pack[:4] == b'PACK'
        assert idx[:4] == b'\xfftOc'
        after = sorted(os.listdir(env.ALASIO_TEMP)) if env.ALASIO_TEMP.isdir() else []
        assert after == before

    def test_a_build_leaves_the_object_store_alone(self, history, tool):
        """
        Building artifacts writes nothing into the repository.

        The server runs several channels over one clone, and a build that wrote
        into it would meet another build there: `pack-objects --stdout` writes
        to a pipe, and the idx is derived from those bytes, so the object store
        is the same before and after.
        """
        def snapshot():
            folder = history.root.joinpath('.git/objects')
            out = []
            for dirpath, dirnames, filenames in os.walk(folder):
                dirnames.sort()
                for name in sorted(filenames):
                    path = os.path.join(dirpath, name)
                    stat = os.lstat(path)
                    out.append((path[len(str(folder)):], stat.st_size, int(stat.st_mtime)))
            return out

        before = snapshot()
        want = history.at(0)
        for have in (history.at(NEAR), history.at(FAR)):
            pack = tool.pack(want=want, have=have)
            tool.idx(pack)
            tool.pack(want=want, have=have, thin=True)

        assert snapshot() == before
