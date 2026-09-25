"""
Lookback commits of a repository, the old versions that still have packs.

The pack server generates full packs of the latest commit and of the
lookback commits, and an update pack from every lookback commit to the
latest commit, see PackRepoModel for the pack files.

Note: the parent selection is a known open issue, the current implementation
walks every parent of every commit, see doc/2026-09-25_lookback-parent-and-merge-message.md
"""

import heapq
import time

from alasio.ext.cache import cached_property
from alasio.logger import logger

# Seconds of a day, the unit of MaxCommitDay and MaxTagDay
SECONDS_PER_DAY = 86400
# Bound of the resolution of a chain of nested tag objects, a legitimate
# chain is 1~3 deep, a deeper one is a crafted repository. Cycles are caught
# by the visited set of _resolve_commit(), this bound only limits the work of
# a crafted acyclic chain, which costs one object read per level
MAX_TAG_CHAIN = 64


class PackRepoLookback:
    """
    Compute the lookback commits of a repo, by the lookback config.

    There is exactly one latest commit: the head of RepoConfig.Branch, the
    branch to pack. It is the version of the latest pack, it is packed on its
    own, and it is never a lookback commit.

    Every other commit of the sources below is a lookback commit, an old
    version that still has a pack and an update pack to the latest commit.
    The sources are independent, so that a tagged version stays updateable
    even when its commit is out of the commit lookback:

    1. Commits of the branches to lookback, limited by MaxCommitCount and
       MaxCommitDay. Both restrictions are applied, the stricter one wins,
       0 means no limit. The branches are RepoConfig.Branch and
       LookbackConfig.LookbackBranch: the head of RepoConfig.Branch is the
       latest commit, the heads of LookbackConfig.LookbackBranch are ordinary
       lookback commits, they are not the latest of anything. Every commit
       reachable from a branch head is walked (every parent of every commit),
       so the commits of a branch that was merged into the branch are
       included: a client could have run them. Note that feature branches of
       a pull request are included too, see the note below.
    2. Commits pointed to by tags, limited by MaxTagCount and MaxTagDay, the
       stricter restriction wins. Tags are counted from the newest tag.
    3. Commits in AdditionalCommit, all restrictions are ignored.

    Duplicated commits are removed, lookback_commit is sorted by the commit
    time of the committer, the newest commit first. Commits of the same time
    keep the order of their source: the branches to lookback in the order of
    the config, then the commits of the tags, then AdditionalCommit.

    Note that the repo objects are read lazily when the repo was not read yet.

    Note that every parent of a commit is walked, so the commits of a feature
    branch of a pull request are lookback commits too, though a client never
    runs them. Which parent to follow is a known open issue, the plan is to
    read it from the merge message, see
    doc/2026-09-25_lookback-parent-and-merge-message.md
    """

    def __init__(self, repo, config):
        """
        Args:
            repo (GitRepo | MockGitRepo): Git repo to lookback
            config (PackRepoModel): Config of the repo, the Lookback field is used
        """
        self.repo = repo
        self.config = config

    @cached_property
    def latest_commit(self):
        """
        Get the latest commit to pack, the head of the branch of RepoConfig.Branch

        The repo has exactly one latest commit, the head of the configured
        branch. The branch comes from the config, so the packs are always
        generated from the configured branch instead of the branch that the
        repo happens to be checked out on.

        Returns:
            str: Commit sha1

        Raises:
            ValueError: If the repo has no such branch
        """
        branch = self.config.Repo.Branch
        sha1 = self.repo.ref_get(f'refs/heads/{branch}')
        if not sha1:
            raise ValueError(f'No such branch "{branch}" at repo {self.repo.path}')
        return sha1

    @cached_property
    def lookback_commit(self):
        """
        Get the commits to pack in addition to the latest commit

        Returns:
            list[str]: Commit sha1s, newest commit first, the latest commit
                of the repo is never included
        """
        latest = self.latest_commit
        self._read_repo()
        now = int(time.time())

        # dict to deduplicate commits, the sources also decide the order of
        # the commits of the same time
        out = {}
        for head in self._iter_walk_head():
            for sha1, commit in self._iter_commit_chain(head, now):
                out[sha1] = commit
        for sha1, commit in self._iter_tag_commit(now):
            out[sha1] = commit
        for sha1, commit in self._iter_additional_commit():
            out[sha1] = commit
        # the latest commit is packed separately
        out.pop(latest, None)

        # sort by the commit time, the sort is stable so commits of the
        # same time keep the order of their source
        return sorted(out, key=lambda sha1: out[sha1].committer_time, reverse=True)

    def _iter_walk_head(self):
        """
        Iter the head of every branch whose history is walked

        The head of the branch to pack comes first, see RepoConfig.Branch: it
        is the latest commit of the repo, the only one. Then the heads of the
        branches of LookbackConfig.LookbackBranch, they are ordinary lookback
        commits, not the latest of anything. A branch that does not exist in
        the repo is skipped with a warning: the versions of the other
        branches are still packed, so a wrong branch of the config does not
        stop the whole repo.

        Yields:
            str: Commit sha1, the head of a branch
        """
        branch = self.config.Repo.Branch
        yield self.latest_commit
        for name in self.config.Lookback.LookbackBranch:
            if not name or name == branch:
                continue
            sha1 = self.repo.ref_get(f'refs/heads/{name}')
            if not sha1:
                logger.warning(f'PackRepoLookback: no such branch "{name}" at repo {self.repo.path}')
                continue
            yield sha1

    def _iter_commit_chain(self, head, now):
        """
        Iter the commits of a branch, from its head backwards

        Every commit reachable from the branch head is walked: the commits
        are popped from a max heap in the order of the commit time, and all
        parents of every popped commit are queued, so the two sides of a
        merge are walked together. A commit reachable through several paths
        is yielded once.

        The walk stops at the first restriction reached: the count of
        commits, or the day of a commit. Note that the commit the walk starts
        from is counted too, like the have_lookback of list_commit_have().

        Args:
            head (str): Commit sha1 to start from, the head of a branch
            now (int): Current unix timestamp in seconds, the reference of
                MaxCommitDay

        Yields:
            tuple[str, CommitObj]: (commit sha1, commit object), newest first
        """
        config = self.config.Lookback
        max_count = config.MaxCommitCount
        max_day = config.MaxCommitDay
        min_time = now - max_day * SECONDS_PER_DAY if max_day else 0

        commit = self._get_commit(head)
        if commit is None:
            return

        # max heap of (-commit time, sha1), so the newest commit is popped
        # first, commits of the same time are ordered by sha1
        queue = [(-commit.committer_time, head)]
        # commits already queued, a commit is reachable through several paths
        seen = {head}
        count = 0

        while queue:
            if max_count and count >= max_count:
                break
            _, sha1 = heapq.heappop(queue)
            commit = self._get_commit(sha1)
            if commit is None:
                continue
            if min_time and commit.committer_time < min_time:
                # the queue is ordered by time, the rest are all out of the day limit
                break

            yield sha1, commit
            count += 1

            for parent in self._iter_parent(commit):
                if parent in seen:
                    continue
                seen.add(parent)
                parent_commit = self._get_commit(parent)
                if parent_commit is None:
                    continue
                heapq.heappush(queue, (-parent_commit.committer_time, parent))

    @staticmethod
    def _iter_parent(commit):
        """
        Iter the parents of a commit, a merge commit has several parents

        Args:
            commit (CommitObj):

        Yields:
            str: Parent commit sha1
        """
        parent = commit.parent
        parent_type = type(parent)
        if parent_type is str:
            yield parent
        elif parent_type is list:
            yield from parent

    def _iter_tag_commit(self, now):
        """
        Iter the commits pointed to by the tags of the repo

        Tags are sorted by the tagger time, the newest tag first, and are
        limited by the count and the day of the tags. A tag that points to a
        blob / tree instead of a commit is skipped, a nested tag object is
        resolved to the commit it points to.

        Args:
            now (int): Current unix timestamp in seconds, the reference of
                MaxTagDay

        Yields:
            tuple[str, CommitObj]: (commit sha1, commit object), newest tag first
        """
        config = self.config.Lookback
        max_count = config.MaxTagCount
        max_day = config.MaxTagDay
        min_time = now - max_day * SECONDS_PER_DAY if max_day else 0

        # read all tags, newest tag first
        list_tag = []
        for name in self.repo.tags:
            try:
                tag = self.repo.tag_get(name)
            except KeyError:
                logger.warning(f'PackRepoLookback: tag "{name}" points to an object that does not exist, '
                               f'repo={self.repo.path}')
                continue
            if tag is None:
                # tag points to a blob or a tree, not a commit
                continue
            list_tag.append(tag)
        list_tag.sort(key=lambda tag: tag.tagger_time, reverse=True)

        count = 0
        for tag in list_tag:
            if max_count and count >= max_count:
                break
            if min_time and tag.tagger_time < min_time:
                # tags are sorted by tagger time, the rest are all out of the day limit
                break
            count += 1

            resolved = self._resolve_commit(tag.object)
            if resolved is None:
                logger.warning(f'PackRepoLookback: tag "{tag.tag}" does not point to a commit, '
                               f'repo={self.repo.path}')
                continue
            yield resolved

    def _iter_additional_commit(self):
        """
        Iter the commits in AdditionalCommit, all lookback restrictions are ignored

        An additional commit that does not exist in the repo is skipped with
        a warning, so that a wrong config does not stop the whole repo.

        Yields:
            tuple[str, CommitObj]: (commit sha1, commit object), in the order of the config
        """
        for sha1 in self.config.Lookback.AdditionalCommit:
            commit = self._get_commit(sha1)
            if commit is None:
                # a broken additional commit, the listed commits are extra
                # versions, skipping it does not break the latest packs
                continue
            yield sha1, commit

    def _resolve_commit(self, sha1):
        """
        Resolve a tag object to the commit it points to

        The chain of nested tag objects is walked with a visited set, so a
        cyclic chain is detected instead of walked forever. A well-formed git
        repo cannot have such a cycle: a tag object is content addressed, it
        cannot contain its own sha1, and two tag objects cannot contain each
        other's sha1. But the object reader trusts the file names and does not
        verify the content hash, so a malformed or crafted repo can have a
        cycle, and the pack server must not hang on it.

        Args:
            sha1 (str): Object sha1

        Returns:
            tuple[str, CommitObj] | None: (commit sha1, commit object), None if
                the object is not a commit, does not exist, or the tag chain
                is cyclic or longer than MAX_TAG_CHAIN tag objects
        """
        seen = set()
        # count of the tag objects resolved, the commit that ends the chain
        # does not count
        count = 0
        while True:
            if sha1 in seen:
                logger.warning(f'PackRepoLookback: cyclic tag chain at {sha1}, repo={self.repo.path}')
                return None
            seen.add(sha1)

            try:
                obj = self.repo.cat(sha1)
            except KeyError:
                logger.warning(f'PackRepoLookback: object {sha1} does not exist, repo={self.repo.path}')
                return None
            if obj.type == 1:
                return sha1, obj.decoded
            if obj.type != 4:
                return None

            # nested tag object, points to another object
            count += 1
            if count > MAX_TAG_CHAIN:
                logger.warning(f'PackRepoLookback: tag chain is too long at {sha1}, repo={self.repo.path}')
                return None
            sha1 = obj.decoded.object

    def _get_commit(self, sha1):
        """
        Read a commit object of the repo

        Args:
            sha1 (str): Commit sha1

        Returns:
            CommitObj | None: None if the object does not exist, or is not a commit
        """
        try:
            obj = self.repo.cat(sha1)
        except KeyError:
            logger.warning(f'PackRepoLookback: object {sha1} does not exist, repo={self.repo.path}')
            return None
        if obj.type != 1:
            logger.warning(f'PackRepoLookback: object {sha1} is not a commit, type={obj.type}')
            return None
        return obj.decoded

    def _read_repo(self):
        """
        Read the repo index lazily

        cat() looks up objects in the index built by read_lazy(), an unread
        repo returns KeyError for every object.
        """
        if not (getattr(self.repo, 'dict_object', None) or getattr(self.repo, 'dict_object_unread', None)):
            self.repo.read_lazy()
