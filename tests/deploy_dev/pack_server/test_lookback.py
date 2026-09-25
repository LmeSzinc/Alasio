"""
Tests for PackRepoLookback: compute the commits to generate packs from.

A MockGitRepo is built with a linear chain of commits, then the lookback
commits are verified against the commit / tag / additional commit
restrictions of the lookback config.
"""
import time

import pytest

from alasio.deploy_dev.pack_server.lookback import MAX_TAG_CHAIN, PackRepoLookback
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.git.stage.hashobj import blob_hash
from alasio.logger import logger

DAY = 86400


def _make_chain(times, branch='master'):
    """
    Build a mock repo of a linear commit chain on a branch.

    The commits are named 'c0', 'c1', ..., 'c{i}' is the parent of 'c{i+1}',
    the branch and the head are the newest commit.

    Args:
        times (list[int]): Committer time of each commit, oldest commit first
        branch (str): Branch to register, default to the default Branch of RepoConfig

    Returns:
        MockGitRepo: Repo
    """
    repo = MockGitRepo()
    parent = None
    for index, time_ in enumerate(times):
        sha1 = f'c{index}'
        repo.register_commit(sha1, parents=[parent] if parent else None, author_time=time_)
        parent = sha1
    repo.register_branch(branch, parent)
    repo.register_head(parent)
    return repo


def _make_branch(repo, sha1, branch='master'):
    """
    Set the head and register the branch of a mock repo.

    Args:
        repo (MockGitRepo): Repo
        sha1 (str): Commit sha1 of the branch head
        branch (str): Branch name, default to the default Branch of RepoConfig

    Returns:
        MockGitRepo: The repo
    """
    repo.register_branch(branch, sha1)
    repo.register_head(sha1)
    return repo


def _make_config(max_commit_count=0, max_commit_day=0, max_tag_count=0, max_tag_day=0, additional=None,
                 lookback_branch=None):
    """
    Build a pack config, every lookback restriction is 0 (no limit) by default.

    Args:
        max_commit_count (int): LookbackConfig.MaxCommitCount
        max_commit_day (int): LookbackConfig.MaxCommitDay
        max_tag_count (int): LookbackConfig.MaxTagCount
        max_tag_day (int): LookbackConfig.MaxTagDay
        additional: LookbackConfig.AdditionalCommit
        lookback_branch: LookbackConfig.LookbackBranch

    Returns:
        PackRepoModel: Config
    """
    return PackRepoModel(Lookback=LookbackConfig(
        MaxCommitCount=max_commit_count,
        MaxCommitDay=max_commit_day,
        MaxTagCount=max_tag_count,
        MaxTagDay=max_tag_day,
        LookbackBranch=lookback_branch if lookback_branch is not None else [],
        AdditionalCommit=additional if additional is not None else [],
    ))


def _make_feature_merge_repo(times):
    """
    Build a mock repo of a pull request from a feature branch, the author
    merged the target branch into the feature branch to solve the conflicts:

        m1 - m2 ------------------ M2 (master, the head)
          \\                         /
           d1 --- M (dev, the head) /
            \\   /
             fm  (feature, m2 merged into f2)
            /  \\
          f1 - f2

    Args:
        times (dict[str, int]): Committer time of each commit

    Returns:
        MockGitRepo: Repo, dev is the branch of the config, master is a lookback branch
    """
    repo = MockGitRepo()
    repo.register_commit('m1', author_time=times['m1'])
    repo.register_commit('m2', parents=['m1'], author_time=times['m2'])
    # the feature branch, the author commits to it and merges master into it
    repo.register_commit('f1', parents=['d1'], author_time=times['f1'])
    repo.register_commit('f2', parents=['f1'], author_time=times['f2'])
    repo.register_commit('fm', parents=['f2', 'm2'], author_time=times['fm'])
    # the pull request is merged into dev, then dev is merged into master
    repo.register_commit('d1', parents=['m1'], author_time=times['d1'])
    repo.register_commit('dev_merge', parents=['d1', 'fm'], author_time=times['dev_merge'])
    repo.register_commit('master_merge', parents=['m2', 'dev_merge'], author_time=times['master_merge'])
    repo.register_branch('dev', 'dev_merge')
    repo.register_branch('master', 'master_merge')
    repo.register_head('dev_merge')
    return repo


class TestLatestCommit:
    """The latest commit is the head of the configured branch."""

    def test_branch(self):
        """The latest commit is the head of the branch of the config."""
        lookback = PackRepoLookback(_make_chain([100, 200, 300]), _make_config())
        assert lookback.latest_commit == 'c2'

    def test_branch_not_head(self):
        """The configured branch is used, not the branch the repo is on."""
        repo = _make_chain([100, 200, 300])
        repo.register_branch('dev', 'c1')
        config = _make_config()
        config.Repo.Branch = 'dev'
        assert PackRepoLookback(repo, config).latest_commit == 'c1'

    def test_no_such_branch(self):
        """A branch that does not exist raises ValueError."""
        repo = _make_chain([100, 200, 300])
        config = _make_config()
        config.Repo.Branch = 'dev'
        with pytest.raises(ValueError) as e:
            PackRepoLookback(repo, config).latest_commit
        assert 'No such branch "dev" at repo' in str(e.value)

    def test_cached(self):
        """latest_commit and lookback_commit are cached, new commits do not change them."""
        repo = _make_chain([100, 200, 300])
        lookback = PackRepoLookback(repo, _make_config())
        assert lookback.latest_commit == 'c2'
        assert lookback.lookback_commit == ['c1', 'c0']

        repo.register_commit('c3', parents=['c2'], author_time=400)
        repo.register_head('c3')
        assert lookback.latest_commit == 'c2'
        assert lookback.lookback_commit == ['c1', 'c0']


class TestCommitChain:
    """Lookback commits of the commit chain."""

    def test_all_commits(self):
        """Every commit of the chain is a lookback commit, the latest commit is not."""
        repo = _make_chain([100, 200, 300, 400])
        lookback = PackRepoLookback(repo, _make_config())
        assert lookback.lookback_commit == ['c2', 'c1', 'c0']

    def test_single_commit(self):
        """A repo of a single commit has no lookback commit."""
        repo = _make_chain([100])
        lookback = PackRepoLookback(repo, _make_config())
        assert lookback.lookback_commit == []

    def test_max_commit_count(self):
        """MaxCommitCount counts the latest commit too, like have_lookback of list_commit_have()."""
        repo = _make_chain([100, 200, 300, 400, 500])
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=4))
        assert lookback.lookback_commit == ['c3', 'c2', 'c1']

    def test_max_commit_day(self):
        """Commits before MaxCommitDay days are not lookback commits."""
        now = int(time.time())
        repo = _make_chain([now - 31 * DAY, now - 29 * DAY, now - 10 * DAY, now - DAY, now])
        lookback = PackRepoLookback(repo, _make_config(max_commit_day=30))
        assert lookback.lookback_commit == ['c3', 'c2', 'c1']

    def test_stricter_restriction_wins(self):
        """The stricter one of MaxCommitCount and MaxCommitDay wins."""
        now = int(time.time())
        repo = _make_chain([now - 10 * DAY, now - 5 * DAY, now - DAY, now])
        # the count allows every commit, the day cuts at c1
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=5, max_commit_day=3))
        assert lookback.lookback_commit == ['c2']
        # the day allows every commit, the count cuts at c1
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=2, max_commit_day=100))
        assert lookback.lookback_commit == ['c2']

    def test_merge_commit_all_parents(self):
        """A merge commit walks every parent, the merged branch is walked too."""
        repo = MockGitRepo()
        repo.register_commit('c1', author_time=1000)
        repo.register_commit('c2', parents=['c1'], author_time=2000)
        repo.register_commit('side', parents=['c1'], author_time=2500)
        repo.register_commit('merge', parents=['c2', 'side'], author_time=3000)
        _make_branch(repo, 'merge')
        lookback = PackRepoLookback(repo, _make_config())
        assert lookback.lookback_commit == ['side', 'c2', 'c1']

    def test_merged_branch_included(self):
        """
        The commits of a branch that was merged into the branch are lookback
        commits, a client could have run them. Note that this includes the
        commits of a feature branch, which is a known open issue, see
        doc/2026-09-25_lookback-parent-and-merge-message.md
        """
        now = int(time.time())
        repo = MockGitRepo()
        # the branch: m1 -> m2
        repo.register_commit('m1', author_time=now - 5 * DAY)
        repo.register_commit('m2', parents=['m1'], author_time=now - 2 * DAY)
        # a feature branch: m1 -> f1 -> f2, merged into the branch
        repo.register_commit('f1', parents=['m1'], author_time=now - 4 * DAY)
        repo.register_commit('f2', parents=['f1'], author_time=now - 3 * DAY)
        repo.register_commit('merge', parents=['m2', 'f2'], author_time=now - DAY)
        _make_branch(repo, 'merge')
        lookback = PackRepoLookback(repo, _make_config())
        assert lookback.lookback_commit == ['m2', 'f2', 'f1', 'm1']

    def test_merged_branch_limit(self):
        """MaxCommitCount counts the newest commits of every merged branch together."""
        now = int(time.time())
        repo = MockGitRepo()
        repo.register_commit('m1', author_time=now - 5 * DAY)
        repo.register_commit('m2', parents=['m1'], author_time=now - 2 * DAY)
        repo.register_commit('f1', parents=['m1'], author_time=now - 4 * DAY)
        repo.register_commit('merge', parents=['m2', 'f1'], author_time=now - DAY)
        _make_branch(repo, 'merge')
        # the 3 newest commits are merge, m2 and f1
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=3))
        assert lookback.lookback_commit == ['m2', 'f1']

    def test_merged_branch_day(self):
        """MaxCommitDay applies to the commits of the merged branch too."""
        now = int(time.time())
        repo = MockGitRepo()
        repo.register_commit('m1', author_time=now - 100 * DAY)
        repo.register_commit('m2', parents=['m1'], author_time=now - 2 * DAY)
        repo.register_commit('f1', parents=['m1'], author_time=now - 3 * DAY)
        repo.register_commit('merge', parents=['m2', 'f1'], author_time=now - DAY)
        _make_branch(repo, 'merge')
        # m1 is out of the day limit, its child on the merged branch is not
        lookback = PackRepoLookback(repo, _make_config(max_commit_day=30))
        assert lookback.lookback_commit == ['m2', 'f1']

    def test_unknown_parent(self):
        """A parent that does not exist stops the chain with a warning."""
        repo = MockGitRepo()
        repo.register_commit('c1', parents=['gone'], author_time=1000)
        _make_branch(repo, 'c1')
        with logger.mock_capture_writer() as capture:
            lookback = PackRepoLookback(repo, _make_config())
            assert lookback.lookback_commit == []
        assert capture.fd.any_contains('object gone does not exist')


class TestLookbackBranch:
    """The commits of the branches to lookback, LookbackConfig.LookbackBranch."""

    def test_lookback_branch(self):
        """The branch of the config and the lookback branches are all walked."""
        now = int(time.time())
        repo = _make_feature_merge_repo({
            'm1': now - 10 * DAY, 'd1': now - 8 * DAY, 'f1': now - 7 * DAY, 'f2': now - 6 * DAY,
            'm2': now - 5 * DAY, 'fm': now - 4 * DAY, 'dev_merge': now - 2 * DAY,
            'master_merge': now - 1 * DAY,
        })
        config = _make_config(lookback_branch=['master'])
        config.Repo.Branch = 'dev'
        lookback = PackRepoLookback(repo, config)
        assert lookback.latest_commit == 'dev_merge'
        # every commit of the two branches, the latest commit is never included
        assert lookback.lookback_commit == ['master_merge', 'fm', 'm2', 'f2', 'f1', 'd1', 'm1']

    def test_feature_branch_included(self):
        """
        The commits of the feature branch of a pull request are lookback
        commits too, which is a known open issue: which parent to follow is
        planned to be read from the merge message, see
        doc/2026-09-25_lookback-parent-and-merge-message.md
        """
        now = int(time.time())
        repo = _make_feature_merge_repo({
            'm1': now - 10 * DAY, 'd1': now - 8 * DAY, 'f1': now - 7 * DAY, 'f2': now - 6 * DAY,
            'm2': now - 5 * DAY, 'fm': now - 4 * DAY, 'dev_merge': now - 2 * DAY,
            'master_merge': now - 1 * DAY,
        })
        config = _make_config(lookback_branch=['master'])
        config.Repo.Branch = 'dev'
        lookback = set(PackRepoLookback(repo, config).lookback_commit)
        # f2 is the head of the feature branch, fm merged master into it
        assert 'f1' in lookback
        assert 'f2' in lookback
        assert 'fm' in lookback
        # while the commits of both branches are included too
        assert {'master_merge', 'm2', 'd1', 'm1'} <= lookback

    def test_lookback_branch_missing(self):
        """A lookback branch that does not exist is skipped with a warning."""
        repo = _make_chain([100, 200, 300])
        config = _make_config(lookback_branch=['dev'])
        with logger.mock_capture_writer() as capture:
            lookback = PackRepoLookback(repo, config)
            assert lookback.lookback_commit == ['c1', 'c0']
        assert capture.fd.any_contains('no such branch "dev" at repo')

    def test_lookback_branch_duplicated(self):
        """The branch to pack may be listed in LookbackBranch, its commits are not duplicated."""
        repo = _make_chain([100, 200, 300])
        config = _make_config(lookback_branch=['master'])
        lookback = PackRepoLookback(repo, config)
        assert lookback.lookback_commit == ['c1', 'c0']


class TestTagCommit:
    """Lookback commits of the tags."""

    def test_tag_out_of_commit_lookback(self):
        """A tagged commit is a lookback commit though it is out of the commit lookback."""
        now = int(time.time())
        repo = _make_chain([now - 300 * DAY, now])
        repo.register_tag('v1.0', 'c0')
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
        assert lookback.lookback_commit == ['c0']

    def test_max_tag_day(self):
        """Tags before MaxTagDay days are not lookback commits."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now - 200 * DAY, now])
        repo.register_tag('v_old', 'c0')
        repo.register_tag('v_new', 'c1')
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=1, max_tag_day=365))
        assert lookback.lookback_commit == ['c1']

    def test_max_tag_count(self):
        """Only the newest MaxTagCount tags are lookback commits."""
        now = int(time.time())
        repo = _make_chain([now - 300 * DAY, now - 200 * DAY, now - 100 * DAY, now])
        repo.register_tag('v1.0', 'c0')
        repo.register_tag('v2.0', 'c1')
        repo.register_tag('v3.0', 'c2')
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=1, max_tag_count=2))
        assert lookback.lookback_commit == ['c2', 'c1']

    def test_tag_sorted_by_tagger_time(self):
        """Tags are counted from the newest tag, not from the registration order."""
        now = int(time.time())
        repo = _make_chain([now - 300 * DAY, now - 200 * DAY, now])
        # the oldest tag is registered first
        repo.register_tag('v_old', 'c0')
        repo.register_tag('v_new', 'c1')
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=1, max_tag_count=1))
        assert lookback.lookback_commit == ['c1']

    def test_annotated_tag(self):
        """An annotated tag is counted by its tagger time, not by the commit time."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_tag_object('tag1', object='c0', tag='v1.0', tagger_time=now - 10 * DAY)
        repo.register_tag('v1.0', 'tag1')
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=1, max_tag_day=30))
        assert lookback.lookback_commit == ['c0']

    def test_nested_tag_object(self):
        """A tag object that points to another tag object is resolved to the commit."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_tag_object('tag2', object='c0', tag='v1.0', tagger_time=now - 10 * DAY)
        repo.register_tag_object('tag1', object='tag2', tag='v1.0', tagger_time=now - 10 * DAY)
        repo.register_tag('v1.0', 'tag1')
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
        assert lookback.lookback_commit == ['c0']

    def test_duplicated_tag_commit(self):
        """Tags of the same commit do not duplicate the lookback commit."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_tag('v1.0', 'c0')
        repo.register_tag_object('tag1', object='c0', tag='v1.0', tagger_time=now - 10 * DAY)
        repo.register_tag('v1.0a', 'tag1')
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
        assert lookback.lookback_commit == ['c0']

    def test_tag_to_blob(self):
        """A tag that points to a blob is skipped."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_file('c1', 'a.txt', b'hello')
        repo.register_tag('v1.0', blob_hash(b'hello'))
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
        assert lookback.lookback_commit == []

    def test_tag_object_to_blob(self):
        """A tag object that points to a blob is skipped with a warning."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_file('c1', 'a.txt', b'hello')
        repo.register_tag_object(
            'tag1', object=blob_hash(b'hello'), tag='v1.0', tagger_time=now, object_type='blob'
        )
        repo.register_tag('v1.0', 'tag1')
        with logger.mock_capture_writer() as capture:
            lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
            assert lookback.lookback_commit == []
        assert capture.fd.any_contains('tag "v1.0" does not point to a commit')

    def test_tag_object_to_unknown_object(self):
        """A tag object that points to a missing object is skipped with warnings."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_tag_object('tag1', object='gone', tag='v1.0', tagger_time=now)
        repo.register_tag('v1.0', 'tag1')
        with logger.mock_capture_writer() as capture:
            lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
            assert lookback.lookback_commit == []
        assert capture.fd.any_contains('object gone does not exist')
        assert capture.fd.any_contains('tag "v1.0" does not point to a commit')

    def test_tag_to_unknown_object(self):
        """A tag that points to a missing object is skipped with a warning."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_tag('v1.0', 'gone')
        with logger.mock_capture_writer() as capture:
            lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
            assert lookback.lookback_commit == []
        assert capture.fd.any_contains('tag "v1.0" points to an object that does not exist')


class TestTagChain:
    """The resolution of a chain of nested tag objects."""

    def test_cyclic_tag_chain(self):
        """Two tag objects that point to each other are detected, not looped forever."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        # a chain of tags: tag_a -> tag_b -> tag_a
        repo.register_tag_object('tag_a', object='tag_b', tag='v1.0', tagger_time=now)
        repo.register_tag_object('tag_b', object='tag_a', tag='v1.0', tagger_time=now)
        repo.register_tag('v1.0', 'tag_a')
        with logger.mock_capture_writer() as capture:
            lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
            assert lookback.lookback_commit == []
        assert capture.fd.any_contains('cyclic tag chain')

    def test_self_referencing_tag(self):
        """A tag object that points to itself is detected."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_tag_object('tag_a', object='tag_a', tag='v1.0', tagger_time=now)
        repo.register_tag('v1.0', 'tag_a')
        with logger.mock_capture_writer() as capture:
            lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
            assert lookback.lookback_commit == []
        assert capture.fd.any_contains('cyclic tag chain')

    @staticmethod
    def _register_tag_chain(repo, count, now):
        """
        Register a chain of tag objects ending at c0 of the chain repo

        The chain is tag_0 -> c0, tag_1 -> tag_0, ..., and the tag ref
        registered by the caller points to tag_{count-1}, so resolving walks
        the tag objects tag_{count-1} .. tag_0, count - 1 of them.

        Args:
            repo (MockGitRepo): Repo of _make_chain(), the chain ends at c0
            count (int): Number of tag objects to register
            now (int): Tagger time

        Returns:
            str: Name of the deepest tag object, the one to register as a tag ref
        """
        sha1 = 'c0'
        for index in range(count):
            name = f'tag_{index}'
            repo.register_tag_object(name, object=sha1, tag='v1.0', tagger_time=now)
            sha1 = name
        return sha1

    def test_tag_chain_at_limit(self):
        """A chain of MAX_TAG_CHAIN tag objects is resolved."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_tag('v1.0', self._register_tag_chain(repo, MAX_TAG_CHAIN + 1, now))
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
        assert lookback.lookback_commit == ['c0']

    def test_tag_chain_too_long(self):
        """A chain longer than MAX_TAG_CHAIN tag objects is refused, not walked."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        repo.register_tag('v1.0', self._register_tag_chain(repo, MAX_TAG_CHAIN + 2, now))
        with logger.mock_capture_writer() as capture:
            lookback = PackRepoLookback(repo, _make_config(max_commit_count=1))
            assert lookback.lookback_commit == []
        assert capture.fd.any_contains('tag chain is too long')


class TestAdditionalCommit:
    """Lookback commits of AdditionalCommit, all restrictions are ignored."""

    def test_ignore_restrictions(self):
        """An additional commit is a lookback commit though it is out of every restriction."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        config = _make_config(max_commit_count=1, max_commit_day=1, max_tag_day=1, additional=['c0'])
        lookback = PackRepoLookback(repo, config)
        assert lookback.lookback_commit == ['c0']

    def test_invalid_commit(self):
        """An additional commit that does not exist is skipped with a warning."""
        now = int(time.time())
        repo = _make_chain([now - 400 * DAY, now])
        config = _make_config(max_commit_count=1, additional=['gone', 'c0'])
        with logger.mock_capture_writer() as capture:
            lookback = PackRepoLookback(repo, config)
            assert lookback.lookback_commit == ['c0']
        assert capture.fd.any_contains('object gone does not exist')

    def test_default_config(self):
        """The default config of the model works, there is no additional commit."""
        now = int(time.time())
        repo = _make_chain([now - DAY, now])
        lookback = PackRepoLookback(repo, PackRepoModel())
        assert lookback.lookback_commit == ['c0']


class TestLookbackOrder:
    """The lookback commits are sorted by the commit time, the newest first."""

    def test_all_sources(self):
        """The commits of every source are listed, the newest commit first."""
        now = int(time.time())
        repo = _make_chain([now - 300 * DAY, now - 200 * DAY, now - 100 * DAY, now])
        repo.register_tag('v_old', 'c0')
        config = _make_config(max_commit_count=2, additional=['c1'])
        # the chain gives c2, the tag gives c0, the additional commit gives c1
        lookback = PackRepoLookback(repo, config)
        assert lookback.lookback_commit == ['c2', 'c1', 'c0']

    def test_sorted_by_commit_time(self):
        """The commits are sorted by their own time, not by the source nor by the time of the tag."""
        now = int(time.time())
        repo = _make_chain([now - 300 * DAY, now - 200 * DAY, now])
        # a commit of another branch, newer than the chain commits
        repo.register_commit('other', author_time=now - 100 * DAY)
        # the chain gives c1, the additional commit gives other which is newer
        lookback = PackRepoLookback(repo, _make_config(max_commit_count=2, additional=['other']))
        assert lookback.lookback_commit == ['other', 'c1']

    def test_no_duplicate(self):
        """A commit listed by multiple sources is listed once."""
        now = int(time.time())
        repo = _make_chain([now - 300 * DAY, now - 200 * DAY, now])
        repo.register_tag('v1.0', 'c1')
        config = _make_config(additional=['c1', 'c0'])
        lookback = PackRepoLookback(repo, config)
        assert lookback.lookback_commit == ['c1', 'c0']

    def test_same_time_keeps_source_order(self):
        """Commits of the same time keep the order of the sources: chain, tag, additional."""
        now = int(time.time())
        repo = _make_chain([now - 300 * DAY, now])
        # a commit of another branch, same time as the chain commit
        repo.register_commit('other', author_time=now - 300 * DAY)
        config = _make_config(additional=['other'])
        lookback = PackRepoLookback(repo, config)
        assert lookback.lookback_commit == ['c0', 'other']


class TestReadRepo:
    """The repo objects are read on demand."""

    def test_unread_repo(self):
        """An unread repo is read before the objects are looked up."""
        repo = _make_chain([100, 200])
        calls = []
        original = repo.read_lazy

        def read_lazy(skip_size=None):
            calls.append(1)
            return original(skip_size=skip_size)

        repo.read_lazy = read_lazy
        lookback = PackRepoLookback(repo, _make_config())
        assert lookback.lookback_commit == ['c0']
        assert calls == [1]

    def test_read_repo(self):
        """A repo that was read is not read again, see GitObjectManager.dict_object."""
        repo = _make_chain([100, 200])
        repo.dict_object = {'c0': None}

        def read_lazy(skip_size=None):
            raise AssertionError('read_lazy() should not be called on a read repo')

        repo.read_lazy = read_lazy
        lookback = PackRepoLookback(repo, _make_config())
        assert lookback.lookback_commit == ['c0']
