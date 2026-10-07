"""
Tests for LookbackWheel: sample the dependency files over the lookback window.

A MockGitRepo is built with a chain of commits, and dependency files are
registered per commit: the blob sha1 of the content is the identity of a
revision, so the same content registered under several commits is one
revision. The files to sample come from PythonDeps.RequirementFiles of the
config; latest_deps (the pins of the latest commit) and lookback_deps (the
pins of the old commits, except the versions latest_deps has) are verified
against the pins of the files and the window of the lookback config.
"""
import pytest

from alasio.deploy_dev.pack_server.lookback_wheel import LookbackWheel
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel, PythonDepsConfig
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger


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


def _make_config(max_commit_count=0, additional=None, files=()):
    """
    Build a pack config, every lookback restriction is 0 (no limit) by default.

    Args:
        max_commit_count (int): LookbackConfig.MaxCommitCount
        additional: LookbackConfig.AdditionalCommit
        files: PythonDepsConfig.RequirementFiles, the dependency files to sample

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
        PythonDeps=PythonDepsConfig(RequirementFiles=list(files)),
    )


def _register(repo, commit, path, content):
    """
    Register the content of a dependency file under a commit.

    Args:
        repo (MockGitRepo): Repo
        commit (str): Commit sha1
        path (str): Path of the file in the repo
        content (str): Content of the file, encoded as UTF-8
    """
    repo.register_file(commit, path, content.encode('utf-8'))


class TestLookbackWheelSample:
    """Sample the dependency files of the window into latest_deps and lookback_deps."""

    def test_latest_commit(self):
        """The file of the latest commit is sampled into latest_deps, everything carries the commit."""
        repo = _make_chain([100])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c0'}
        assert wheel.lookback_deps == {}

    def test_old_and_latest_version(self):
        """The pins of the latest commit and the old versions are split apart."""
        repo = _make_chain([100, 200, 300])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.27.8\ntrio==0.34.0\n')
        _register(repo, 'c2', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c2'}
        assert wheel.lookback_deps == {
            ('httpx', '0.27.8'): 'c1',
            ('trio', '0.34.0'): 'c1',
        }
        # the entries keep the order of the sample: the files and the pins
        # of the latest commit first, then the lookback commits newest first
        assert list(wheel.latest_deps) == [('httpx', '0.28.1')]
        assert list(wheel.lookback_deps) == [('httpx', '0.27.8'), ('trio', '0.34.0')]

    def test_version_of_latest_not_in_lookback(self):
        """A version the latest commit asks for is not in lookback_deps, whatever old commits ask."""
        repo = _make_chain([100, 200, 300])
        for commit in ('c0', 'c1', 'c2'):
            _register(repo, commit, 'requirements.txt', 'httpx==0.28.1\nstarlette==1.6.0\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {
            ('httpx', '0.28.1'): 'c2',
            ('starlette', '1.6.0'): 'c2',
        }
        assert wheel.lookback_deps == {}

    def test_mixed_names(self):
        """A name with an old version and a current version lives on both sides."""
        repo = _make_chain([100, 200])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\nstarlette==1.6.0\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\nstarlette==1.6.0\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {
            ('httpx', '0.28.1'): 'c1',
            ('starlette', '1.6.0'): 'c1',
        }
        assert wheel.lookback_deps == {('httpx', '0.27.8'): 'c0'}

    def test_version_dropped_in_latest(self):
        """A version that only old commits ask for keeps the newest of them."""
        repo = _make_chain([100, 200, 300])
        _register(repo, 'c0', 'requirements.txt', 'trio==0.34.0\n')
        _register(repo, 'c1', 'requirements.txt', 'trio==0.34.0\n')
        _register(repo, 'c2', 'requirements.txt', 'trio==0.35.0\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {('trio', '0.35.0'): 'c2'}
        assert wheel.lookback_deps == {('trio', '0.34.0'): 'c1'}

    def test_file_added_later(self):
        """A file that does not exist at an old commit is skipped there."""
        repo = _make_chain([100, 200])
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c1'}
        assert wheel.lookback_deps == {}

    def test_empty_files(self):
        """A config of no dependency file samples nothing."""
        repo = _make_chain([100])
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.files == []
        assert wheel.latest_deps == {}
        assert wheel.lookback_deps == {}

    def test_files_from_config(self):
        """The files to sample are PythonDeps.RequirementFiles, a file that is not listed is not read."""
        repo = _make_chain([100, 200])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        _register(repo, 'c0', 'other.txt', 'trio==0.34.0\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.files == ['requirements.txt']
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c1'}
        assert wheel.lookback_deps == {('httpx', '0.27.8'): 'c0'}

    def test_scan_commit(self):
        """The scan samples the latest commit first, then the lookback commits newest first."""
        repo = _make_chain([100, 200, 300])
        wheel = LookbackWheel(repo, _make_config())
        assert wheel.scan_commit == ['c2', 'c1', 'c0']

    def test_cached(self):
        """The sampled mappings and scan_commit are cached, a new commit does not change them."""
        repo = _make_chain([100, 200])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c1'}
        assert wheel.latest_deps is wheel.latest_deps
        assert wheel.lookback_deps == {('httpx', '0.27.8'): 'c0'}
        assert wheel.lookback_deps is wheel.lookback_deps

        repo.register_commit('c2', parents=['c1'], author_time=300)
        repo.register_branch('master', 'c2')
        repo.register_head('c2')
        _register(repo, 'c2', 'requirements.txt', 'httpx==0.29.0\n')
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c1'}
        assert wheel.lookback_deps == {('httpx', '0.27.8'): 'c0'}
        assert wheel.scan_commit == ['c1', 'c0']


class TestLookbackWheelWindow:
    """The window of the lookback config limits the sample."""

    def test_max_commit_count(self):
        """The commits out of the window are not sampled."""
        repo = _make_chain([100, 200, 300, 400])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        _register(repo, 'c2', 'requirements.txt', 'httpx==0.27.9\n')
        _register(repo, 'c3', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(max_commit_count=2, files=['requirements.txt']))
        # the window is c3 (the latest commit) and c2, the versions of c0 are out
        assert wheel.scan_commit == ['c3', 'c2']
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c3'}
        assert wheel.lookback_deps == {('httpx', '0.27.9'): 'c2'}

    def test_additional_commit(self):
        """An additional commit is sampled though it is out of the window."""
        repo = _make_chain([100, 200, 300])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.27.8\n')
        wheel = LookbackWheel(repo, _make_config(max_commit_count=1, additional=['c0'], files=['requirements.txt']))
        assert wheel.latest_deps == {}
        assert wheel.lookback_deps == {('httpx', '0.27.8'): 'c0'}

    def test_no_lookback_commit(self):
        """A repo of a single commit samples the latest commit only."""
        repo = _make_chain([100])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(max_commit_count=1, files=['requirements.txt']))
        assert wheel.scan_commit == ['c0']
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c0'}
        assert wheel.lookback_deps == {}


class TestLookbackWheelFiles:
    """The parser of a file is chosen by its name, both kinds are sampled."""

    def test_pyproject_and_requirements(self):
        """A pyproject.toml is parsed as TOML, every other file as requirements."""
        repo = _make_chain([100])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        _register(repo, 'c0', 'backend/pyproject.toml',
                  '[project]\ndependencies = ["starlette==1.6.0"]\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt', 'backend/pyproject.toml']))
        assert wheel.latest_deps == {
            ('httpx', '0.28.1'): 'c0',
            ('starlette', '1.6.0'): 'c0',
        }
        assert wheel.lookback_deps == {}

    def test_name_normalized(self):
        """The name of an entry is the PEP 503 normalized one, like the dist-key."""
        repo = _make_chain([100])
        _register(repo, 'c0', 'requirements.txt', 'ruamel.yaml==0.18.6\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {('ruamel-yaml', '0.18.6'): 'c0'}

    def test_range_skipped(self):
        """A file that only carries a range constraint yields no entry."""
        repo = _make_chain([100])
        _register(repo, 'c0', 'requirements.txt', 'httpx>=0.27,<0.29\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {}
        assert wheel.lookback_deps == {}

    def test_missing_file(self):
        """A path that exists at no commit of the window is warned, not raised."""
        repo = _make_chain([100])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        with logger.mock_capture_writer() as capture:
            wheel = LookbackWheel(repo, _make_config(files=['requirements.txt', 'requirements-dev.txt']))
            assert wheel.latest_deps == {('httpx', '0.28.1'): 'c0'}
        assert capture.fd.any_contains('no such dependency file "requirements-dev.txt"')

    def test_file_added_later_no_warning(self):
        """A file that only the latest commit carries is not warned about."""
        repo = _make_chain([100, 200])
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.1\n')
        with logger.mock_capture_writer() as capture:
            wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
            assert wheel.latest_deps == {('httpx', '0.28.1'): 'c1'}
            assert wheel.lookback_deps == {}
        assert not capture.fd.any_contains('no such dependency file')

    def test_same_revision_parsed_once(self):
        """The same revision of a file is read once, the two sides share the parse."""
        repo = _make_chain([100, 200, 300])
        for commit in ('c0', 'c1', 'c2'):
            _register(repo, commit, 'requirements.txt', 'httpx==0.28.1\n')
        calls = []
        cat = repo.cat

        def spy_cat(sha1):
            calls.append(sha1)
            return cat(sha1)

        repo.cat = spy_cat
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c2'}
        assert wheel.lookback_deps == {}
        blob = repo.get_file('c0', 'requirements.txt').sha1
        assert calls.count(blob) == 1


class TestLookbackWheelConflict:
    """A state of the repo the sample cannot read."""

    def test_conflicting_files(self):
        """Two files of the latest commit that ask for two versions of one name raise."""
        repo = _make_chain([100])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        _register(repo, 'c0', 'pyproject.toml', '[project]\ndependencies = ["httpx==0.28.2"]\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt', 'pyproject.toml']))
        with pytest.raises(ValueError) as e:
            _ = wheel.latest_deps
        assert 'Conflicting versions of "httpx"' in str(e.value)
        assert '0.28.1' in str(e.value)
        assert '0.28.2' in str(e.value)

    def test_conflict_in_an_old_commit(self):
        """A disagreement of an old commit raises too, the old version cannot be told."""
        repo = _make_chain([100, 200])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        _register(repo, 'c0', 'pyproject.toml', '[project]\ndependencies = ["httpx==0.28.2"]\n')
        _register(repo, 'c1', 'requirements.txt', 'httpx==0.28.2\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt', 'pyproject.toml']))
        with pytest.raises(ValueError, match='Conflicting versions of "httpx"'):
            _ = wheel.lookback_deps

    def test_same_version_is_one_pin(self):
        """Two files that ask for the same version are one entry."""
        repo = _make_chain([100])
        _register(repo, 'c0', 'requirements.txt', 'httpx==0.28.1\n')
        _register(repo, 'c0', 'pyproject.toml', '[project]\ndependencies = ["httpx==0.28.1"]\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt', 'pyproject.toml']))
        assert wheel.latest_deps == {('httpx', '0.28.1'): 'c0'}
        assert wheel.lookback_deps == {}

    def test_invalid_file(self):
        """A file that cannot be parsed names the file and the commit."""
        repo = _make_chain([100, 200])
        _register(repo, 'c0', 'pyproject.toml', '[project\n')
        wheel = LookbackWheel(repo, _make_config(files=['pyproject.toml']))
        with pytest.raises(ValueError) as e:
            _ = wheel.lookback_deps
        assert 'Failed to parse "pyproject.toml" at commit c0' in str(e.value)

    def test_not_utf8(self):
        """A file that is not UTF-8 names the file and the commit."""
        repo = _make_chain([100])
        repo.register_file('c0', 'requirements.txt', b'# \xff\xfe\nhttpx==0.28.1\n')
        wheel = LookbackWheel(repo, _make_config(files=['requirements.txt']))
        with pytest.raises(ValueError) as e:
            _ = wheel.latest_deps
        assert 'Failed to parse "requirements.txt" at commit c0' in str(e.value)
