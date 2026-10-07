"""
Look back the dependency files of a repo, the wheels the lookback window asks for.

A pack config with PythonDeps builds the packs of the dependencies of a repo,
the versions to build are the versions the repository asked for in its
dependency files. This module samples the dependency files of the config
over the lookback window and answers the input of the wheel fetch, as two
mappings of (name, version) to the commit that asks for it:

- latest_deps: the pins of the latest commit, the head of the branch to pack,
  the version a client updates to. They are the target of an update: every
  one of them gets a full pack, and every version of lookback_deps gets an
  update path to them;
- lookback_deps: the pins of the old commits, except the versions
  latest_deps has: a version the latest commit still asks for is the target,
  not an old version, whatever old commits ask. What is left is the versions
  a client may still be on, every one of them gets a full pack and an update
  path to the target.

Every dependency file of the config is read at every commit of the window:

- the content is read from the git repository (get_file, cat), not from the
  working tree: the file of an old commit is read as the commit wrote it;
- a file that does not exist at a commit is skipped there, e.g. a file that a
  later commit added; a file that exists at no commit of the window is
  reported with a warning, the config may name a wrong path;
- every revision of a file is parsed once: the parse result is cached by the
  blob sha1 of the revision, the commits that did not change the file share
  one parse;
- a version that several commits ask for keeps the newest commit, the commits
  are sampled newest first.

A dependency without an exact version (a range constraint, a bare name, a
URL) is skipped by the parser of the file and yields no wheel; the other
declarations of the file are read as they are. A commit that asks for two
different versions of one name (e.g. its requirements.txt and its
pyproject.toml disagree) raises ValueError: the version to build cannot be
told, and a silent pick would build the wrong wheel.

Usage:
    from alasio.deploy_dev.pack_server.lookback_wheel import LookbackWheel

    wheel = LookbackWheel(repo, config)
    wheel.latest_deps
    wheel.lookback_deps
"""

from alasio.deploy_dev.pack_server.lookback import PackRepoLookback
from alasio.deploy_dev.pack_server.parse_dep import PyprojectParser, RequirementsParser
from alasio.ext.cache import cached_property
from alasio.logger import logger


def _is_pyproject(path):
    """
    Whether a dependency file path is parsed as a pyproject.toml.

    The file name decides the parser: a file named 'pyproject.toml', at the
    root of the repo or in any folder of it, carries the TOML dependency
    tables of the project; every other file is a requirements file.

    Args:
        path (str): Path of a dependency file in the repo

    Returns:
        bool: True if the file is parsed by PyprojectParser, False by
            RequirementsParser
    """
    return path.rpartition('/')[2] == 'pyproject.toml'


class LookbackWheel:
    """
    Sample the dependency files of a repo over the lookback window.

    The class only reads the git repo, the caller opens it and passes it in,
    like PackRepoLookback; nothing is written and no mirror is touched, the
    wheels of the mappings are fetched by WheelFetcher, which this class does
    not use.

    The files to sample are the paths of PythonDeps.RequirementFiles of the
    config, the same files the wheel fetch and the pack build work from.
    latest_deps is the pins of the latest commit, the target of an update;
    lookback_deps is the pins of the old commits that the latest commit does
    not ask for, the versions a client may still be on. Both map
    (name, version) to the commit that asks for the version, the entries of
    latest_deps carry the latest commit.

    Usage:
        wheel = LookbackWheel(repo, config)
        wheel.latest_deps
        wheel.lookback_deps
    """

    def __init__(self, repo, config):
        """
        Args:
            repo (GitRepo | MockGitRepo): Git repo to sample, every revision
                of a dependency file is read from it and nothing is written,
                like PackRepoLookback
            config (PackRepoModel): Config of the repo, read from a
                PackRepoConfig; the Lookback field is the window of the
                commits, the PythonDeps field holds the files to sample and
                the dependencies to build
        """
        self.repo = repo
        self.config = config
        # the latest commit and the window of the commits to sample, the
        # same object the git flow uses
        self.lookback = PackRepoLookback(repo, config)

    @cached_property
    def files(self):
        """
        The dependency files to sample: the paths of PythonDeps.RequirementFiles.

        A path is relative to the root of the repo and uses '/', like a git
        path; the parser of a file is chosen by its name, see _is_pyproject.

        Returns:
            list[str]: Paths of the dependency files, e.g.
                ['requirements.txt', 'pyproject.toml']
        """
        return self.config.PythonDeps.RequirementFiles

    @cached_property
    def scan_commit(self):
        """
        The commits to sample, the latest commit first.

        The latest commit is the head of the branch to pack, the only latest
        one; the lookback commits are the old versions that still have packs
        and an update pack to the latest commit, see
        PackRepoLookback.lookback_commit. The list is cached on the
        instance: build a new instance for the next run of the server.

        Returns:
            list[str]: Commit sha1s, the latest commit first, then the
                lookback commits, newest first

        Raises:
            ValueError: If the repo has no branch to pack, see
                PackRepoLookback.latest_commit
        """
        # lookback_commit reads the git objects lazily (PackRepoLookback),
        # the sampling of latest_deps and lookback_deps needs every object
        # of these commits
        return [self.lookback.latest_commit, *self.lookback.lookback_commit]

    @cached_property
    def latest_deps(self):
        """
        The dependencies the latest commit asks for: the target of an update.

        The latest commit is the head of the branch to pack, the version a
        client updates to. Its pins are the target: every one of them gets a
        full pack, and every version of lookback_deps gets an update path to
        them.

        Returns:
            dict[tuple[str, str], str]: (PEP 503 normalized name, version) ->
                commit sha1, the latest commit, in the order the files and
                the pins were read

        Raises:
            ValueError: If the repo has no branch to pack, a commit asks for
                two different versions of one name, or a dependency file
                cannot be read, see _sample: the two mappings are sampled in
                one pass, a problem of any commit of the window raises here
                too
        """
        return self._sample[0]

    @cached_property
    def lookback_deps(self):
        """
        The dependencies the old commits ask for, except the ones of latest_deps.

        A version the latest commit still asks for is the target, not an old
        version, whatever an old commit asks: the key is not in this
        mapping. What is left is every version an old commit asks for that
        the latest commit does not: a version a client may still be on,
        every one of them gets an update path to the target.

        Returns:
            dict[tuple[str, str], str]: (PEP 503 normalized name, version) ->
                commit sha1 of the newest lookback commit that asks for the
                version, in the order the entries were found: the lookback
                commits newest first, the files in the order of the config

        Raises:
            ValueError: If the repo has no branch to pack, a commit asks for
                two different versions of one name, or a dependency file
                cannot be read, see _sample: the two mappings are sampled in
                one pass, a problem of any commit of the window raises here
                too
        """
        return self._sample[1]

    @cached_property
    def _sample(self):
        """
        Sample both sides of the window in one pass: (latest_deps, lookback_deps).

        One pass shares the parse cache between the latest commit and the
        lookback commits, and the warning of a path that exists at no commit
        of the window needs the files of both sides read, so the two
        mappings are computed together and the first read of either one
        computes both.

        Returns:
            tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
                The mappings of latest_deps (first) and lookback_deps (second)

        Raises:
            ValueError: If the repo has no branch to pack (see
                PackRepoLookback.latest_commit), a commit asks for two
                different versions of one name, or a dependency file cannot
                be read: not UTF-8, not a TOML document, or two different
                versions of one name in the file
        """
        # the parse cache of file revisions, keyed by (blob sha1, pyproject):
        # a file that was not changed for many commits has one blob, it is
        # read and parsed once and every commit shares the result
        cache = {}
        # the files found at a commit, to warn about a path of the config
        # that no commit of the window holds
        found = set()

        # the pins of the latest commit: the version a client updates to
        latest_commit = self.lookback.latest_commit
        latest_deps = {
            (name, version): latest_commit
            for name, version in self._parse_commit(latest_commit, cache, found).items()
        }

        lookback_deps = {}
        for commit in self.lookback.lookback_commit:
            for name, version in self._parse_commit(commit, cache, found).items():
                key = (name, version)
                # a version the latest commit still asks for is the target,
                # not an old version, whatever an old commit asks
                if key in latest_deps:
                    continue
                # the commits are sampled newest first, the first commit
                # that asks for a version is the newest one
                if key not in lookback_deps:
                    lookback_deps[key] = commit

        for path in self.files:
            if path not in found:
                logger.warning(
                    f'LookbackWheel: no such dependency file "{path}" in the lookback window, '
                    f'repo={self.repo.path}')

        return latest_deps, lookback_deps

    def _parse_commit(self, commit, cache, found):
        """
        Parse every dependency file of one commit into the pins of the commit.

        Args:
            commit (str): Commit sha1 to sample
            cache (dict): Parse cache of file revisions, see _sample, keyed
                by (blob sha1, pyproject)
            found (set): Paths found at a commit, updated in place

        Returns:
            dict[str, str]: name -> version of every pin of the commit; a
                name that several files of the commit ask for keeps one
                version, the files must agree

        Raises:
            ValueError: If two files of the commit ask for two different
                versions of one name
        """
        pins = {}
        # the file of a pin, for the conflict message
        paths = {}
        for path in self.files:
            entry = self.repo.get_file(commit, path)
            if entry is None:
                # the file does not exist at this commit, e.g. a later
                # commit added it, nothing to parse here
                continue
            found.add(path)

            pyproject = _is_pyproject(path)
            key = (entry.sha1, pyproject)
            parsed = cache.get(key)
            if parsed is None:
                parsed = self._parse_revision(entry.sha1, path, commit)
                cache[key] = parsed

            for name, version in parsed.items():
                other = pins.get(name)
                if other is None:
                    pins[name] = version
                    paths[name] = path
                elif other != version:
                    raise ValueError(
                        f'Conflicting versions of "{name}" at commit {commit}: '
                        f'"{other}" in "{paths[name]}" and "{version}" in "{path}"')
        return pins

    def _parse_revision(self, sha1, path, commit):
        """
        Read and parse one revision of a dependency file.

        The blob is read from the git repo, the working tree of the repo is
        not looked at; the parser is chosen by the file name, see
        _is_pyproject. The names of the pins are normalized with PEP 503 by
        the parser, like the dist-key of the pack files.

        Args:
            sha1 (str): Blob sha1 of the revision
            path (str): Path of the file in the repo
            commit (str): Commit the revision belongs to, for the error message

        Returns:
            dict[str, str]: name -> version of the exact pins of the revision

        Raises:
            ValueError: If the revision is not UTF-8, or the parser cannot
                read the content: not a TOML document, or two different
                versions of one name in the file
        """
        try:
            content = self.repo.cat(sha1).decoded.decode('utf-8')
            if _is_pyproject(path):
                return PyprojectParser(content).dict_deps
            return RequirementsParser(content).dict_deps
        except (UnicodeDecodeError, ValueError) as e:
            raise ValueError(f'Failed to parse "{path}" at commit {commit}: {e}') from e
