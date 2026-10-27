"""
Tests for LookbackWheel: sample the dependency file of a repo over the window.

A MockGitRepo is built with a chain of commits, and the mod entry and the
dependency file are registered per commit: the blob sha1 of the content is the
identity of a revision, so the same content registered under several commits
is one revision. The dependency file of a commit is the one its own mod entry
names, so the entry is registered first: latest_deps (name -> pin of the
latest commit) and lookback_deps ((name, version) -> pin of an old version the
latest commit does not ask for) are verified against the pins of the files and
the window of the lookback config, the index of a pin included.
"""
from types import SimpleNamespace

import pytest

from alasio.deploy.simple_pip.parse_req import DepInfo
from alasio.deploy_dev.pack_server import parse_entry
from alasio.deploy_dev.pack_server.lookback_wheel import LookbackWheel
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel
from alasio.deploy_dev.pack_server.parse_entry import ENTRY_FILE
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger


def pin(name, version, source=''):
    """
    Build the pin a dependency file of the mock repo asks for.

    Args:
        name (str): PEP 503 normalized name
        version (str): Pinned version
        source (str): Normalized index of the '# from' annotation. Defaults
            to '', the pin annotates none

    Returns:
        DepInfo: The expected pin
    """
    return DepInfo(name=name, version=version, source=source)


def entry_source(requirements_file='requirements.txt'):
    """
    Build the source of a mod entry that declares a dependency file.

    Args:
        requirements_file (str): ModEntryInfo.requirements_file to declare.
            Defaults to 'requirements.txt', the same path as the default

    Returns:
        str: Source of the entry module
    """
    return f"entry = ModEntryInfo(name='m', requirements_file={requirements_file!r})\n"


def _make_chain(times):
    """
    Build a mock repo of a linear commit chain on the branch of the config.

    The commits are named 'c0', 'c1', ..., 'c{i}' is the parent of 'c{i+1}',
    the branch and the head are the newest commit.

    Args:
        times (list[int]): Committer time of each commit, oldest commit first

    Returns:
        MockGitRepo: Repo
    """
    repo = MockGitRepo()
    parent = None
    for index, time_ in enumerate(times):
        sha1 = f'c{index}'
        repo.register_commit(sha1, parents=[parent] if parent else None, author_time=time_)
        parent = sha1
    repo.register_branch('master', parent)
    repo.register_head(parent)
    return repo


def _make_config(max_commit_count=0, additional=None):
    """
    Build a pack config, every lookback restriction is 0 (no limit) by default.

    Args:
        max_commit_count (int): LookbackConfig.MaxCommitCount
        additional: LookbackConfig.AdditionalCommit

    Returns:
        PackRepoModel: Config
    """
    return PackRepoModel(
        Lookback=LookbackConfig(
            MaxCommitCount=max_commit_count,
            MaxCommitDay=0,
            MaxTagCount=0,
            MaxTagDay=0,
            AdditionalCommit=additional if additional is not None else [],
        ),
    )


def _register(repo, commit, path, content):
    """
    Register the content of a file under a commit.

    Args:
        repo (MockGitRepo): Repo
        commit (str): Commit sha1
        path (str): Path of the file in the repo
        content (str): Content of the file, encoded as UTF-8
    """
    repo.register_file(commit, path, content.encode('utf-8'))


def _register_entry(repo, commit, requirements_file='requirements.txt'):
    """
    Register the mod entry of a commit.

    Args:
        repo (MockGitRepo): Repo
        commit (str): Commit sha1
        requirements_file (str): Dependency file the entry declares.
            Defaults to 'requirements.txt'
    """
    _register(repo, commit, ENTRY_FILE, entry_source(requirements_file))


class TestLookbackWheelSample:
    """Sample the dependency files of the window into latest_deps and lookback_deps."""

    def test_latest_commit(self):
        """The file of the latest commit is sampled into latest_deps."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.lookback_deps == {}

    def test_old_and_latest_version(self):
        """The pins of the latest commit and the old versions are split apart."""
        repo = _make_chain([100, 200, 300])
        for commit in ('c0', 'c1', 'c2'):
            _register_entry(repo, commit)
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.27.8\ntrio==0.34.0\n')
        _register(repo, 'c2', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.lookback_deps == {
            ('httpx', '0.27.8'): pin('httpx', '0.27.8'),
            ('trio', '0.34.0'): pin('trio', '0.34.0'),
        }
        # the entries keep the order of the sample: the pins of the latest
        # commit first, then the lookback commits newest first
        assert list(wheel.latest_deps) == ['httpx']
        assert list(wheel.lookback_deps) == [('httpx', '0.27.8'), ('trio', '0.34.0')]

    def test_several_lookback_versions_of_one_name(self):
        """A name with several old versions gets one entry per version, newest first."""
        repo = _make_chain([100, 200, 300])
        for commit in ('c0', 'c1', 'c2'):
            _register_entry(repo, commit)
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.7\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.27.8\n')
        _register(repo, 'c2', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        # the commits are sampled newest first, so is the mapping
        assert wheel.lookback_deps == {
            ('httpx', '0.27.8'): pin('httpx', '0.27.8'),
            ('httpx', '0.27.7'): pin('httpx', '0.27.7'),
        }
        assert list(wheel.lookback_deps) == [('httpx', '0.27.8'), ('httpx', '0.27.7')]

    def test_version_of_latest_not_in_lookback(self):
        """A version the latest commit asks for is not in lookback_deps, whatever old commits ask."""
        repo = _make_chain([100, 200, 300])
        for commit in ('c0', 'c1', 'c2'):
            _register_entry(repo, commit)
            _register(repo, commit, 'requirements.txt', 'httpx==0.28.1\nstarlette==1.6.0\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {
            'httpx': pin('httpx', '0.28.1'),
            'starlette': pin('starlette', '1.6.0'),
        }
        assert wheel.lookback_deps == {}

    def test_mixed_names(self):
        """A name with an old version and a current version lives on both sides."""
        repo = _make_chain([100, 200])
        for commit in ('c0', 'c1'):
            _register_entry(repo, commit)
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\nstarlette==1.6.0\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\nstarlette==1.6.0\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {
            'httpx': pin('httpx', '0.28.1'),
            'starlette': pin('starlette', '1.6.0'),
        }
        assert wheel.lookback_deps == {('httpx', '0.27.8'): pin('httpx', '0.27.8')}

    def test_version_dropped_in_latest(self):
        """A version that only old commits ask for keeps the newest of them."""
        repo = _make_chain([100, 200, 300])
        for commit in ('c0', 'c1', 'c2'):
            _register_entry(repo, commit)
        _register(repo, 'c0', 'requirements.txt', 'trio==0.34.0\n')
        _register(repo, 'c1', 'requirements.txt', 'trio==0.34.0\n')
        _register(repo, 'c2', 'requirements.txt', 'trio==0.35.0\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'trio': pin('trio', '0.35.0')}
        assert wheel.lookback_deps == {('trio', '0.34.0'): pin('trio', '0.34.0')}

    def test_file_added_later(self):
        """A dependency file that does not exist at an old commit is skipped there."""
        repo = _make_chain([100, 200])
        _register_entry(repo, 'c0')
        _register_entry(repo, 'c1')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.lookback_deps == {}

    def test_no_entry_file(self):
        """A commit without an entry samples the defaults of ModEntryInfo."""
        repo = _make_chain([100])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.repo_entry['c0'].requirements_file == 'requirements.txt'
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}

    def test_scan_commit(self):
        """The scan samples the latest commit first, then the lookback commits newest first."""
        repo = _make_chain([100, 200, 300])
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.scan_commit == ['c2', 'c1', 'c0']

    def test_cached(self):
        """The sampled mappings and scan_commit are cached, a new commit does not change them."""
        repo = _make_chain([100, 200])
        for commit in ('c0', 'c1'):
            _register_entry(repo, commit)
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.latest_deps is wheel.latest_deps
        assert wheel.lookback_deps == {('httpx', '0.27.8'): pin('httpx', '0.27.8')}
        assert wheel.lookback_deps is wheel.lookback_deps

        repo.register_commit('c2', parents=['c1'], author_time=300)
        repo.register_branch('master', 'c2')
        repo.register_head('c2')
        _register_entry(repo, 'c2')
        _register(repo, 'c2', 'requirements.txt', 'httpx==0.29.0\n')
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.lookback_deps == {('httpx', '0.27.8'): pin('httpx', '0.27.8')}
        assert wheel.scan_commit == ['c1', 'c0']


class TestLookbackWheelEntry:
    """The dependency file of a commit is the one the entry of that commit names."""

    def test_entry_of_each_commit(self):
        """Every commit is sampled from the file its own entry declares."""
        repo = _make_chain([100, 200])
        _register_entry(repo, 'c0', 'old.txt')
        _register_entry(repo, 'c1', 'requirements.txt')
        _register(repo, 'c0', 'old.txt', 'httpx==0.27.8\n')
        # the file of the latest entry does not exist at the old commit, and
        # the file the old entry declares is not read at the latest commit
        _register(repo, 'c0', 'requirements.txt', 'trio==0.34.0\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.lookback_deps == {('httpx', '0.27.8'): pin('httpx', '0.27.8')}

    def test_renamed_file(self):
        """A mod that moved its dependency file keeps both revisions apart."""
        repo = _make_chain([100, 200])
        _register_entry(repo, 'c0', 'requirements.txt')
        _register_entry(repo, 'c1', 'deps/requirements.txt')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\ntrio==0.34.0\n')
        _register(repo, 'c1', 'deps/requirements.txt', 'httpx==0.28.1\ntrio==0.34.0\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {
            'httpx': pin('httpx', '0.28.1'),
            'trio': pin('trio', '0.34.0'),
        }
        assert wheel.lookback_deps == {('httpx', '0.27.8'): pin('httpx', '0.27.8')}

    def test_entry_parsed_once_per_revision(self, monkeypatch):
        """An entry file that did not change is parsed once for the whole window."""
        repo = _make_chain([100, 200, 300])
        for commit in ('c0', 'c1', 'c2'):
            _register_entry(repo, commit)
            _register(repo, commit, 'requirements.txt', 'httpx==0.28.1\n')
        calls = []
        real = parse_entry.parse_mod_entry

        def counting(source, source_name=''):
            calls.append(source_name)
            return real(source, source_name=source_name)

        monkeypatch.setattr(parse_entry, 'parse_mod_entry', counting)
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert len(calls) == 1
        # the three commits share one entry object
        assert wheel.repo_entry['c0'] is wheel.repo_entry['c2']

    def test_entry_changed(self):
        """The entry of every revision is its own, a commit that changed it has another entry."""
        repo = _make_chain([100, 200])
        _register_entry(repo, 'c0', 'requirements.txt')
        _register_entry(repo, 'c1', 'requirements-2.txt')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        _register(repo, 'c1', 'requirements-2.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.repo_entry['c0'].requirements_file == 'requirements.txt'
        assert wheel.repo_entry['c1'].requirements_file == 'requirements-2.txt'
        assert wheel.repo_entry['c0'] is not wheel.repo_entry['c1']

    def test_entry_broken(self):
        """An entry of any commit of the window that cannot be parsed fails the sample."""
        repo = _make_chain([100, 200])
        _register(repo, 'c0', ENTRY_FILE, "entry = ModEntryInfo(name='m', requirements_file=get_file())\n")
        _register_entry(repo, 'c1')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        with pytest.raises(ValueError, match='module/config/const.py at commit c0'):
            _ = wheel.latest_deps

    def test_entry_symlink(self):
        """An entry path that is a symlink is refused, its blob is a path."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        real = repo.get_file

        def get_file(commit, path):
            if path == ENTRY_FILE:
                return SimpleNamespace(sha1='x', mode=b'120000')
            return real(commit, path)

        repo.get_file = get_file
        wheel = LookbackWheel(repo, _make_config())
        with pytest.raises(ValueError, match='is a symlink'):
            _ = wheel.repo_entry

    def test_missing_declared_file_warns(self):
        """The file the latest entry names but the latest commit does not hold is warned."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0', 'requirements-deps.txt')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        with logger.mock_capture_writer() as capture:
            wheel = LookbackWheel(repo, _make_config())
            assert wheel.latest_deps == {}
        assert capture.concat() == f"""\
WARNING | LookbackWheel: no such dependency file "requirements-deps.txt" at the latest commit c0, declared by {ENTRY_FILE}; repo=
"""

    def test_missing_declared_file_in_old_commit_is_silent(self):
        """A file an old entry names but the old commit does not hold is skipped, not warned."""
        repo = _make_chain([100, 200])
        _register_entry(repo, 'c0', 'requirements-deps.txt')
        _register_entry(repo, 'c1', 'requirements-deps.txt')
        _register(repo, 'c1', 'requirements-deps.txt', 'httpx==0.28.1\n')
        with logger.mock_capture_writer() as capture:
            wheel = LookbackWheel(repo, _make_config())
            assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert capture.concat() == ''

    def test_empty_requirements_file(self):
        """An entry that declares an empty path has nothing to sample."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0', '')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        with logger.mock_capture_writer() as capture:
            wheel = LookbackWheel(repo, _make_config())
            assert wheel.latest_deps == {}
        assert capture.concat() == ''


class TestLookbackWheelWindow:
    """The window of the lookback config limits the sample."""

    def test_max_commit_count(self):
        """The commits out of the window are not sampled."""
        repo = _make_chain([100, 200, 300, 400])
        for commit in ('c0', 'c1', 'c2', 'c3'):
            _register_entry(repo, commit)
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        _register(repo, 'c2', 'requirements.txt', 'httpx==0.27.9\n')
        _register(repo, 'c3', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(max_commit_count=2))
        # the window is c3 (the latest commit) and c2, the versions of c0 are out
        assert wheel.scan_commit == ['c3', 'c2']
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.lookback_deps == {('httpx', '0.27.9'): pin('httpx', '0.27.9')}

    def test_additional_commit(self):
        """An additional commit is sampled though it is out of the window."""
        repo = _make_chain([100, 200, 300])
        _register_entry(repo, 'c0')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        wheel = LookbackWheel(repo, _make_config(max_commit_count=1, additional=['c0']))
        assert wheel.latest_deps == {}
        assert wheel.lookback_deps == {('httpx', '0.27.8'): pin('httpx', '0.27.8')}

    def test_no_lookback_commit(self):
        """A repo of a single commit samples the latest commit only."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(max_commit_count=1))
        assert wheel.scan_commit == ['c0']
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.lookback_deps == {}


class TestLookbackWheelFiles:
    """The parser of a file is chosen by its name, the pins are read as they are."""

    def test_pyproject(self):
        """A dependency file named pyproject.toml is parsed as TOML."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0', 'pyproject.toml')
        _register(repo, 'c0', 'pyproject.toml', '[project]\ndependencies = ["httpx==0.28.1"]\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}

    def test_name_normalized(self):
        """The name of an entry is the PEP 503 normalized one, like the dist-key."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0')
        _register(repo, 'c0', 'requirements.txt', 'ruamel.yaml==0.18.6\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'ruamel-yaml': pin('ruamel-yaml', '0.18.6')}

    def test_range_skipped(self):
        """A file that only carries a range constraint yields no entry."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0')
        _register(repo, 'c0', 'requirements.txt', 'httpx>=0.27,<0.29\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {}
        assert wheel.lookback_deps == {}

    def test_same_revision_parsed_once(self):
        """The same revision of a dependency file is read once, the window shares the parse."""
        repo = _make_chain([100, 200, 300])
        for commit in ('c0', 'c1', 'c2'):
            _register_entry(repo, commit)
            _register(repo, commit, 'requirements.txt', 'httpx==0.28.1\n')
        calls = []
        cat = repo.cat

        def spy_cat(sha1):
            calls.append(sha1)
            return cat(sha1)

        repo.cat = spy_cat
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.lookback_deps == {}
        blob = repo.get_file('c0', 'requirements.txt').sha1
        assert calls.count(blob) == 1


class TestLookbackWheelSource:
    """The index a pin came from is sampled with the pin."""

    def test_source_propagated(self):
        """The '# from' annotation of a pin is the source of its entry, both sides of the window."""
        repo = _make_chain([100, 200])
        for commit in ('c0', 'c1'):
            _register_entry(repo, commit)
        _register(repo, 'c0', 'requirements.txt', (
            'httpx==0.27.8\n'
            '    # from http://deps.example/simple/\n'
        ))
        _register(repo, 'c1', 'requirements.txt', (
            'httpx==0.28.1\n'
            '    # from http://deps.example/simple/\n'
        ))
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {
            'httpx': pin('httpx', '0.28.1', 'http://deps.example/simple'),
        }
        assert wheel.lookback_deps == {
            ('httpx', '0.27.8'): pin('httpx', '0.27.8', 'http://deps.example/simple'),
        }

    def test_lookback_source_of_its_own(self):
        """An old dependency file may annotate an index of its own, the pin keeps it."""
        repo = _make_chain([100, 200])
        for commit in ('c0', 'c1'):
            _register_entry(repo, commit)
        _register(repo, 'c0', 'requirements.txt', (
            'httpx==0.27.8\n'
            '    # from http://old.example/simple/\n'
        ))
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert wheel.lookback_deps == {
            ('httpx', '0.27.8'): pin('httpx', '0.27.8', 'http://old.example/simple'),
        }

    def test_source_normalized(self):
        """The source is normalized: the case of the host, the default port and the trailing slash."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0')
        _register(repo, 'c0', 'requirements.txt', (
            'httpx==0.28.1\n'
            '    # from http://DEPS.Example:80/simple/\n'
        ))
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {
            'httpx': pin('httpx', '0.28.1', 'http://deps.example/simple'),
        }

    def test_latest_deps_is_not_filtered(self):
        """latest_deps carries every pin of the latest file, keyed by the PEP 503 name."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\ntrio==0.34.0\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {
            'httpx': pin('httpx', '0.28.1'),
            'trio': pin('trio', '0.34.0'),
        }
        assert list(wheel.latest_deps) == ['httpx', 'trio']

    def test_pyproject_has_no_source(self):
        """A pyproject.toml annotates no index, every pin of it has an empty source."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0', 'pyproject.toml')
        _register(repo, 'c0', 'pyproject.toml', '[project]\ndependencies = ["httpx==0.28.1"]\n')
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}


class TestLookbackWheelConflict:
    """A state of the repo the sample cannot read."""

    def test_conflicting_versions_in_one_file(self):
        """Two versions of one name in one file keep the first, the parser warns."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0')
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\nhttpx==0.28.2\n')
        wheel = LookbackWheel(repo, _make_config())
        with logger.mock_capture_writer() as capture:
            assert wheel.latest_deps == {'httpx': pin('httpx', '0.28.1')}
        assert capture.concat() == """\
WARNING | Conflicting versions of "httpx" in the requirements: "0.28.1" and "0.28.2", keeping "0.28.1"
"""

    def test_invalid_file(self):
        """A file that cannot be parsed names the file and the commit."""
        repo = _make_chain([100, 200])
        _register_entry(repo, 'c0', 'pyproject.toml')
        _register(repo, 'c0', 'pyproject.toml', '[project\n')
        _register_entry(repo, 'c1', 'pyproject.toml')
        _register(repo, 'c1', 'pyproject.toml', '[project]\ndependencies = ["httpx==0.28.1"]\n')
        wheel = LookbackWheel(repo, _make_config())
        with pytest.raises(ValueError) as e:
            _ = wheel.lookback_deps
        assert 'Failed to parse "pyproject.toml" at commit c0' in str(e.value)

    def test_not_utf8(self):
        """A file that is not UTF-8 names the file and the commit."""
        repo = _make_chain([100])
        _register_entry(repo, 'c0')
        repo.register_file('c0', 'requirements.txt', b'# \xff\xfe\nhttpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config())
        with pytest.raises(ValueError) as e:
            _ = wheel.latest_deps
        assert 'Failed to parse "requirements.txt" at commit c0' in str(e.value)
