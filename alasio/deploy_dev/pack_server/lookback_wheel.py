"""
Look back the dependency files of a repo, the pins the lookback window asks for.

A repo declares the python dependencies of a mod in a dependency file, the
file its mod entry names (`ModEntryInfo.requirements_file`). This module
samples that file over the lookback window and answers the input of the wheel
fetch, as two mappings of DepInfo:

- latest_deps: the pins of the latest commit, the head of the branch to pack,
  the version a client updates to, keyed by the PEP 503 normalized name. They
  are the target of an update: every one of them gets a full pack, and every
  version of lookback_deps gets an update path to them;
- lookback_deps: the pins of the old commits, except the versions
  latest_deps has: a version the latest commit still asks for is the target,
  not an old version, whatever old commits ask. What is left is the versions
  a client may still be on, every one of them gets an update path to the
  target, keyed by (name, version).

A pin is a DepInfo: the name, the version, and the index its '# from'
annotation names ('' when the file annotates none). Both sides carry the
source: an old dependency file may annotate an index of its own as well as the
latest one does.

latest_deps is not filtered, it is the pin set of the latest dependency file:
the release side decides what is built (the update_pack_deps of the mod entry)
and what is published (the pins whose source the entry provides).

The file to read is the one the commit itself declares, the version of the mod
the commit is:

- the entry of the commit is read first (module/config/const.py at that
  commit), then the dependency file it names at that commit: the file of an
  old commit is the one that commit declares, not the one the latest commit
  declares now, so a mod that renamed or moved its dependency file is still
  sampled as it asked to be;
- both are read from the git repository (get_file, cat), not from a working
  tree: the revision of an old commit is read as the commit wrote it;
- a commit without an entry file samples the defaults of ModEntryInfo, e.g.
  'requirements.txt', the same values the runtime entry of the mod declares;
- a dependency file that does not exist at a commit is skipped there, e.g. a
  file that a later commit added, and one the entry of the latest commit names
  but the latest commit does not hold is reported with a warning, the entry
  names a wrong path;
- every revision is parsed once: the parse result is cached by the blob sha1
  of the revision, the commits that did not change the file share one parse.
  An entry file rarely changes, so the entry of the whole window is usually
  one parse;
- a version that several commits ask for keeps the newest commit, the commits
  are sampled newest first.

A dependency without an exact version (a range constraint, a bare name, a URL)
is skipped by the parser of the file and yields no wheel; the other
declarations of the file are read as they are.

Usage:
    from alasio.deploy_dev.pack_server.lookback_wheel import LookbackWheel

    wheel = LookbackWheel(repo, config)
    wheel.latest_deps
    wheel.lookback_deps
"""

from alasio.deploy_dev.pack_server.lookback import PackRepoLookback
from alasio.deploy_dev.pack_server.parse_dep import PyprojectParser, RequirementsParser
from alasio.deploy_dev.pack_server.parse_entry import ENTRY_FILE, PackRepoEntry
from alasio.ext.cache import cached_property
from alasio.logger import logger

# the mode of a symbolic link in the index of a git tree
_SYMLINK_MODE = b'120000'


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

    The dependency file of a commit is the one its own mod entry names, see
    the module docstring. latest_deps is the pin set of the latest commit, the
    targets of an update; lookback_deps is the pins of the old commits that
    the latest commit does not ask for, the versions a client may still be on.

    Usage:
        wheel = LookbackWheel(repo, config)
        wheel.latest_deps
        wheel.lookback_deps
    """

    def __init__(self, repo, config):
        """
        Args:
            repo (GitRepo | MockGitRepo): Git repo to sample, every revision
                of the entry and of a dependency file is read from it and
                nothing is written, like PackRepoLookback
            config (PackRepoModel): Config of the repo, read from a
                PackRepoConfig; the Lookback field is the window of the
                commits. The dependency files to sample are declared by the
                mod entry of each commit, not by the config
        """
        self.repo = repo
        self.config = config
        # the latest commit and the window of the commits to sample, the
        # same object the git flow uses
        self.lookback = PackRepoLookback(repo, config)

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
    def repo_entry(self):
        """
        The mod entry of every commit of the window: {commit: PackRepoEntry}.

        The entry file of a commit is read from the git blob of the commit,
        never from a working tree, and the parse is shared by the commits that
        did not change the file: a revision is parsed once per blob sha1. A
        commit without an entry file gets the defaults of ModEntryInfo, the
        entry the runtime of the mod implies.

        Returns:
            dict[str, PackRepoEntry]: Commit sha1 -> entry of that commit

        Raises:
            ValueError: If the repo has no branch to pack, the entry path is a
                symlink (its blob is a path, not the source), or the entry of
                a commit cannot be parsed
        """
        # {blob sha1 | None: PackRepoEntry}, None for the commits that hold
        # no entry file: the entries of a window that did not change the file
        # share one parse
        cache = {}
        out = {}
        for commit in self.scan_commit:
            file = self.repo.get_file(commit, ENTRY_FILE)
            if file is None:
                key = None
            else:
                if file.mode == _SYMLINK_MODE:
                    raise ValueError(
                        f'{ENTRY_FILE} at commit {commit} is a symlink, not a file')
                key = file.sha1
            entry = cache.get(key)
            if entry is None:
                # a commit without an entry file declares the defaults of the
                # model, the empty source parses to them
                source = b'' if file is None else self.repo.cat(file.sha1).decoded
                entry = PackRepoEntry(source, source_name=f'{ENTRY_FILE} at commit {commit}')
                cache[key] = entry
            out[commit] = entry
        return out

    @cached_property
    def repo_dep(self):
        """
        The dependency file of every commit of the window: {commit: parser}.

        The file of a commit is the one the entry of that commit names, read
        from the git blob of the commit; a file that the commit does not hold
        has no entry in the mapping. The parse is shared by the commits that
        did not change the file: a revision is parsed once per blob sha1 and
        file kind.

        Returns:
            dict[str, RequirementsParser | PyprojectParser]: Commit sha1 ->
                parser of the dependency file the commit declares

        Raises:
            ValueError: If the repo has no branch to pack, or a dependency
                file cannot be read: not UTF-8, not a TOML document, or two
                different versions of one name in the file
        """
        latest_commit = self.lookback.latest_commit
        # {(blob sha1, pyproject): parser} of the revisions read so far
        cache = {}
        out = {}
        for commit in self.scan_commit:
            path = self.repo_entry[commit].requirements_file
            if not path:
                # the entry declares no dependency file, nothing to sample
                continue
            file = self.repo.get_file(commit, path)
            if file is None:
                if commit == latest_commit:
                    # the entry of the commit a client updates to names a
                    # file the commit does not hold, the path is a mistake
                    logger.warning(
                        f'LookbackWheel: no such dependency file "{path}" at the latest commit '
                        f'{commit}, declared by {ENTRY_FILE}; repo={self.repo.path}')
                continue
            pyproject = _is_pyproject(path)
            key = (file.sha1, pyproject)
            parser = cache.get(key)
            if parser is None:
                try:
                    content = self.repo.cat(file.sha1).decoded.decode('utf-8')
                except UnicodeDecodeError as e:
                    raise ValueError(
                        f'Failed to parse "{path}" at commit {commit}: {e}') from e
                parser = PyprojectParser(content) if pyproject else RequirementsParser(content)
                try:
                    # the parse runs now, so a broken file names a commit here
                    # instead of at the first read of the pins; the parser
                    # keeps the result for the other commits of the revision
                    _ = parser.dict_pins
                except ValueError as e:
                    raise ValueError(
                        f'Failed to parse "{path}" at commit {commit}: {e}') from e
                cache[key] = parser
            out[commit] = parser
        return out

    @cached_property
    def latest_deps(self):
        """
        The dependencies the latest commit asks for, keyed by their PEP 503 name.

        The latest commit is the head of the branch to pack, the version a
        client updates to. Its pins are the target: every one of them gets a
        full pack, and every version of lookback_deps gets an update path to
        them. The pin set is not filtered: the release side decides what is
        built (the update_pack_deps of the mod entry) and what is published
        (the pins whose source the entry provides). A pin carries the index
        its '# from' annotation names, '' when the file annotates none.

        Returns:
            dict[str, DepInfo]: PEP 503 normalized name -> pin, in the order
                the file declares the pins. The mapping is the cached one of
                the parser of the file, read only.

        Raises:
            ValueError: If the repo has no branch to pack, an entry cannot be
                parsed, or the dependency file cannot be read, see repo_dep
        """
        parser = self.repo_dep.get(self.lookback.latest_commit)
        if parser is None:
            # the latest commit declares no dependency file, or does not hold
            # the one it declares (repo_dep warned about it)
            return {}
        return parser.dict_pins

    @cached_property
    def lookback_deps(self):
        """
        The dependencies the old commits ask for, except the ones of latest_deps.

        A version the latest commit still asks for is the target, not an old
        version, whatever an old commit asks: the key is not in this mapping.
        What is left is every version an old commit asks for that the latest
        commit does not: a version a client may still be on, every one of them
        gets an update path to the target. Every pin carries the index its
        '# from' annotation names, like latest_deps: an old dependency file may
        annotate an index of its own too.

        Returns:
            dict[tuple[str, str], DepInfo]: (PEP 503 normalized name, version)
                -> pin, in the order the entries were found: the lookback
                commits newest first

        Raises:
            ValueError: If the repo has no branch to pack, an entry cannot be
                parsed, or a dependency file cannot be read, see repo_dep
        """
        # a version the latest commit still asks for is the target, not an
        # old version, whatever an old commit asks
        targets = {(name, pin.version) for name, pin in self.latest_deps.items()}

        out = {}
        for commit in self.lookback.lookback_commit:
            parser = self.repo_dep.get(commit)
            if parser is None:
                # the commit declares no dependency file, or does not hold
                # the one it declares: nothing is pinned there
                continue
            for name, pin in parser.dict_pins.items():
                key = (name, pin.version)
                if key in targets or key in out:
                    # the commits are sampled newest first: the first commit
                    # that asks for a version is the newest one
                    continue
                out[key] = pin
        return out
