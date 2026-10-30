"""
Tests for the wheels of the provided pins of a repo: repo_wheel.RepoWheel.

The step downloads every wheel of the pins whose index the mod entry declares
in update_provide_pypi_mirrors to the private cache, and syncs them to the
folder a client installs from:

    {workspace}/_wheel_cache/{name}/{version}/*.whl   the cache
    {workspace}/wheel/{name}/{version}/*.whl          the pip index

The tests run the step on its own, the way repo.Repo wires it. The mirror is
the MockMirror of the fetch tests (an httpx2.MockTransport handler), the git
repo is a MockGitRepo of a linear chain.
"""
import hashlib

import pytest

from alasio.deploy.pip_download.pypi_file import WheelHashError, WheelNotFoundError
from alasio.deploy_dev.pack import pack_wheel
from alasio.deploy_dev.pack._pack_cache import PackCache
from alasio.deploy_dev.pack_server import repo_wheel
from alasio.deploy_dev.pack_server.repo_base import RepoBase
from alasio.deploy_dev.pack_server.repo_dep import RepoDep
from alasio.deploy_dev.pack_server.repo_wheel import RepoWheel
from alasio.ext.path.atomic import atomic_write
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.conftest import join_path
from tests.deploy_dev.pack_server.test_repo import (
    PROVIDED_URL, entry_source, make_config, make_repo, read_tree, register_wheels, requirement
)

# folder of the workspace of the config in the run directory
WORKSPACE = 'pack/Author_Repo_master'

# folder of the private cache of the workspace
CACHE_ROOT = f'{WORKSPACE}/_wheel_cache'

# folder of the wheels a client installs with pip
WHEEL_ROOT = f'{WORKSPACE}/wheel'

# the provided pin of the tests: a compiled dependency, one wheel per platform
MSGS_WHEELS = (
    'msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl',
    'msgspec-0.22.0-cp38-cp38-win_amd64.whl',
)

# the dependency files of the tests: msgspec comes from the index the repo
# provides, httpx from the mirror of the pins (not provided by the repo)
MSGS_REQUIREMENTS = {
    'c1': requirement('httpx==0.27.7'),
    'c2': requirement('httpx==0.27.8'),
    'c3': requirement('httpx==0.28.1') + requirement('msgspec==0.22.0', f'{PROVIDED_URL}/'),
}


@pytest.fixture(autouse=True)
def fresh_wheel_cache(monkeypatch):
    """
    Fresh cache of the wheel pipeline for one test, see test_pack_wheel.
    """
    monkeypatch.setattr(pack_wheel, 'WHEEL_CACHE', PackCache())


class WheelFlow:
    """
    The provided wheels of a repo, for the tests.

    The flow of repo.Repo runs RepoWheel before the dependency packs and the
    repo tree; the tests of this module run the step on its own.
    """

    def __init__(self, repo, config=None, client=None):
        """
        Args:
            repo (MockGitRepo): Git repo to publish
            config (PackRepoModel, optional): Config of the repo. Defaults to
                None, the config of the tests
            client (httpx2.AsyncClient, optional): Client of the mock mirror
        """
        self.base = RepoBase(repo, config if config is not None else make_config(), client)
        self.wheel = RepoWheel(self.base)

    def run(self):
        """Fetch the wheels of the provided pins and sync them to the index."""
        self.wheel.run()

    def commit(self):
        """Remove the wheels the window does not ask for."""
        self.wheel.commit()

    def generate(self):
        """Run then commit, the normal flow of a publication."""
        self.run()
        self.commit()
        return self


def make_provided_repo(requirements=None, entry=None):
    """
    Build the repo of the provided pin of the tests.

    Args:
        requirements (dict, optional): {commit: dependency file}, Defaults to
            None, MSGS_REQUIREMENTS
        entry (str, optional): Source of the mod entry. Defaults to None, an
            entry that provides PROVIDED_URL and declares no pack channel

    Returns:
        MockGitRepo: Repo of the tests
    """
    return make_repo(
        requirements if requirements is not None else MSGS_REQUIREMENTS,
        entry=entry if entry is not None else entry_source(provide=(PROVIDED_URL,)))


def register_msgspec_wheels(mirror):
    """
    Register the platform wheels of msgspec in the mock mirror.

    Args:
        mirror (MockMirror): Mirror to register the wheels in

    Returns:
        list[bytes]: Content of the wheels, in the order of MSGS_WHEELS
    """
    mirror.alias(PROVIDED_URL)
    return [
        mirror.register('msgspec', '0.22.0', tags='cp38-cp38-manylinux_2_17_x86_64'),
        mirror.register('msgspec', '0.22.0', tags='cp38-cp38-win_amd64'),
    ]


def cache_folder(run_dir, name, version):
    """
    Folder of a version in the private cache of the workspace.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/_wheel_cache/{name}/{version}
    """
    return join_path(run_dir, CACHE_ROOT, name, version)


def published_folder(run_dir, name, version):
    """
    Folder of a version in the pip index of the workspace.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/wheel/{name}/{version}
    """
    return join_path(run_dir, WHEEL_ROOT, name, version)


class TestFetch:
    """The wheels of the provided pins, from their own index."""

    def test_every_wheel_of_the_pin(self, fs, run_dir, mirror):
        """Every wheel of the version is fetched and published, one per platform."""
        contents = register_msgspec_wheels(mirror)
        gen = WheelFlow(make_provided_repo(), client=mirror.client)
        gen.run()

        # the cache of the version holds the page and the two wheels
        cache = cache_folder(run_dir, 'msgspec', '0.22.0')
        assert sorted(cache.iter_filenames()) == ['index.html', *MSGS_WHEELS]
        # the pip index holds the two wheels, and nothing else
        published = published_folder(run_dir, 'msgspec', '0.22.0')
        assert sorted(published.iter_filenames()) == list(MSGS_WHEELS)

        wheels = gen.wheel.publish_files[('msgspec', '0.22.0')]
        assert [wheel.file.name for wheel in wheels] == list(MSGS_WHEELS)
        for wheel, content in zip(wheels, contents):
            assert wheel.file == cache.joinpath(wheel.file.name)
            assert wheel.hashes['sha256'] == hashlib.sha256(content).hexdigest()
            assert published.joinpath(wheel.file.name).atomic_read_bytes() == content

    def test_run_log(self, fs, run_dir, mirror):
        """The run reads the page once and logs every transfer and every copy."""
        register_msgspec_wheels(mirror)
        gen = WheelFlow(make_provided_repo(), client=mirror.client)

        with logger.mock_capture_writer() as capture:
            gen.generate()

        cache = cache_folder(run_dir, 'msgspec', '0.22.0')
        published = published_folder(run_dir, 'msgspec', '0.22.0')
        assert capture.concat() == f"""\
INFO | No update_pack_deps declared by module/config/const.py at c3, no dependency pack is built
INFO | [1/1] Fetching wheels of "msgspec==0.22.0", index="http://provided.example/simple"
INFO | Download index page: "http://provided.example/simple/msgspec/"
INFO | Download wheel: "http://provided.example/packages/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl" -> "{cache}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Download wheel: "http://provided.example/packages/msgspec-0.22.0-cp38-cp38-win_amd64.whl" -> "{cache}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
INFO | [1/1] Publishing wheels of "msgspec==0.22.0"
INFO | Copy wheel: "{cache}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl" -> "{published}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Copy wheel: "{cache}/msgspec-0.22.0-cp38-cp38-win_amd64.whl" -> "{published}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
"""
        # the index of the pin is the only page read, not the mirror of the
        # other dependencies
        assert [url for url in mirror.requests if url.endswith('/')] == [
            'http://provided.example/simple/msgspec/']

    def test_latest_only(self, fs, run_dir, mirror):
        """Only the pin of the latest commit is published, the lookbacks are not fetched."""
        register_wheels(mirror)
        mirror.alias(PROVIDED_URL)
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('httpx==0.27.8'),
            'c3': requirement('httpx==0.28.1', f'{PROVIDED_URL}/'),
        }
        entry = entry_source(provide=(PROVIDED_URL,))
        gen = WheelFlow(make_repo(requirements, entry=entry), client=mirror.client)
        gen.run()

        assert list(gen.wheel.publish_files) == [('httpx', '0.28.1')]
        assert cache_folder(run_dir, 'httpx', '0.28.1').isdir()
        assert not cache_folder(run_dir, 'httpx', '0.27.8').exists()
        assert not cache_folder(run_dir, 'httpx', '0.27.7').exists()
        # the page and the wheel of the latest version only
        assert 'http://provided.example/simple/httpx/' in mirror.requests
        assert 'http://mirror/httpx/' not in mirror.requests

    def test_missing_wheel(self, fs, run_dir, mirror):
        """A provided pin without a usable wheel fails the run before a file is copied."""
        mirror.alias(PROVIDED_URL)
        gen = WheelFlow(make_provided_repo(), client=mirror.client)

        with pytest.raises(WheelNotFoundError) as e:
            gen.run()

        assert str(e.value) == (
            'No usable wheel of the provided dependency "msgspec==0.22.0", '
            'the pip index cannot be generated: '
            'No index of "msgspec" in "http://provided.example/simple"')
        assert gen.wheel.publish_files == {}
        assert not join_path(run_dir, WHEEL_ROOT).exists()
        with pytest.raises(RuntimeError, match='run\\(\\) must be called'):
            gen.commit()

    def test_hash_mismatch(self, fs, run_dir, mirror):
        """A wheel that does not match the hash of its link is refused, nothing is published."""
        mirror.alias(PROVIDED_URL)
        mirror.register('msgspec', '0.22.0', tags='cp38-cp38-win_amd64', digest='0' * 64)
        gen = WheelFlow(make_provided_repo(), client=mirror.client)

        with pytest.raises(WheelHashError):
            gen.run()

        assert not join_path(run_dir, WHEEL_ROOT).exists()
        assert gen.wheel.publish_files == {}

    def test_no_provided_pin(self, fs, run_dir, mirror):
        """A repo that provides no index fetches and publishes nothing."""
        register_wheels(mirror)
        gen = WheelFlow(make_repo(entry=entry_source()), client=mirror.client)

        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert capture.concat() == """\
INFO | No update_pack_deps declared by module/config/const.py at c3, no dependency pack is built
"""
        assert mirror.requests == []
        assert gen.wheel.publish_files == {}
        assert not join_path(run_dir, CACHE_ROOT).exists()
        assert not join_path(run_dir, WHEEL_ROOT).exists()

    def test_pack_channel_wheel_is_reused(self, fs, run_dir, mirror):
        """A version the pack step fetched is answered by the cache, no request."""
        register_wheels(mirror)
        mirror.alias(PROVIDED_URL)
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('httpx==0.27.8'),
            'c3': requirement('httpx==0.28.1', f'{PROVIDED_URL}/'),
        }
        # the entry declares httpx as a pack channel and provides its index: the
        # two steps read the same version
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        base = RepoBase(make_repo(requirements, entry=entry), make_config(), mirror.client)
        RepoDep(base).run()
        requests = list(mirror.requests)

        wheel = RepoWheel(base)
        wheel.run()

        # the page and the pure wheel of the version are in the cache, and the
        # version has no other wheel: the second step reads nothing
        assert mirror.requests == requests
        assert [wheel_info.file.name for wheel_info in wheel.publish_files[('httpx', '0.28.1')]] == [
            'httpx-0.28.1-py3-none-any.whl']


class TestSync:
    """The published files, verified against the cache."""

    def test_existing_copy_is_kept(self, fs, run_dir, mirror):
        """A published file with the sha256 of its cache file is kept as it is."""
        register_msgspec_wheels(mirror)
        repo = make_provided_repo()
        WheelFlow(repo, client=mirror.client).generate()
        files = [published_folder(run_dir, 'msgspec', '0.22.0').joinpath(name)
                 for name in MSGS_WHEELS]
        stats = [file.stat().st_mtime_ns for file in files]
        cache = read_tree(cache_folder(run_dir, 'msgspec', '0.22.0'))
        published = read_tree(published_folder(run_dir, 'msgspec', '0.22.0'))
        requests = list(mirror.requests)

        with logger.mock_capture_writer() as capture:
            WheelFlow(repo, client=mirror.client).generate()

        assert capture.concat() == f"""\
INFO | No update_pack_deps declared by module/config/const.py at c3, no dependency pack is built
INFO | [1/1] Fetching wheels of "msgspec==0.22.0", index="http://provided.example/simple"
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'msgspec', '0.22.0')}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'msgspec', '0.22.0')}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
INFO | [1/1] Publishing wheels of "msgspec==0.22.0"
INFO | Wheel exists, skipped: "{published_folder(run_dir, 'msgspec', '0.22.0')}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Wheel exists, skipped: "{published_folder(run_dir, 'msgspec', '0.22.0')}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
"""
        # nothing is read from the network, nothing is written
        assert mirror.requests == requests
        assert [file.stat().st_mtime_ns for file in files] == stats
        assert read_tree(cache_folder(run_dir, 'msgspec', '0.22.0')) == cache
        assert read_tree(published_folder(run_dir, 'msgspec', '0.22.0')) == published

    def test_truncated_copy_is_copied_again(self, fs, run_dir, mirror):
        """A published file of another size is not the copy: it is written again."""
        register_msgspec_wheels(mirror)
        repo = make_provided_repo()
        WheelFlow(repo, client=mirror.client).generate()
        file = published_folder(run_dir, 'msgspec', '0.22.0').joinpath(MSGS_WHEELS[0])
        atomic_write(file, b'truncated')

        with logger.mock_capture_writer() as capture:
            WheelFlow(repo, client=mirror.client).generate()

        assert (f'INFO | Copy wheel: "{cache_folder(run_dir, "msgspec", "0.22.0")}/'
                f'{MSGS_WHEELS[0]}" -> "{file}"\n' in capture.concat())
        assert file.atomic_read_bytes() == cache_folder(
            run_dir, 'msgspec', '0.22.0').joinpath(MSGS_WHEELS[0]).atomic_read_bytes()

    def test_same_size_copy_is_copied_again(self, fs, run_dir, mirror):
        """A published file of the right size but another content is written again."""
        register_msgspec_wheels(mirror)
        repo = make_provided_repo()
        WheelFlow(repo, client=mirror.client).generate()
        file = published_folder(run_dir, 'msgspec', '0.22.0').joinpath(MSGS_WHEELS[1])
        content = file.atomic_read_bytes()
        atomic_write(file, b'\x00' * len(content))

        with logger.mock_capture_writer() as capture:
            WheelFlow(repo, client=mirror.client).generate()

        assert (f'INFO | Copy wheel: "{cache_folder(run_dir, "msgspec", "0.22.0")}/'
                f'{MSGS_WHEELS[1]}" -> "{file}"\n' in capture.concat())
        assert file.atomic_read_bytes() == content


class TestCleanup:
    """The wheels the window does not ask for are removed."""

    def test_run_keeps_the_previous_index(self, fs, run_dir, mirror):
        """run() only writes: a stale version goes with commit()."""
        register_msgspec_wheels(mirror)
        stale = published_folder(run_dir, 'msgspec', '0.21.0')
        fs.create_file(join_path(stale, 'msgspec-0.21.0-py3-none-any.whl'), contents=b'stale')
        gen = WheelFlow(make_provided_repo(), client=mirror.client)

        gen.run()
        assert stale.isdir()

        gen.commit()
        assert not stale.exists()
        assert published_folder(run_dir, 'msgspec', '0.22.0').isdir()

    def test_commit_requires_run(self, fs, run_dir, mirror):
        """commit() before run() cannot remove the wheels of a failed run."""
        gen = WheelFlow(make_provided_repo(), client=mirror.client)
        with pytest.raises(RuntimeError, match='run\\(\\) must be called'):
            gen.commit()

    def test_stale_published_version_removed(self, fs, run_dir, mirror):
        """A version the latest commit does not pin anymore is removed."""
        register_msgspec_wheels(mirror)
        fs.create_file(join_path(published_folder(run_dir, 'msgspec', '0.21.0'), 'index.html'),
                       contents=b'stale')

        with logger.mock_capture_writer() as capture:
            WheelFlow(make_provided_repo(), client=mirror.client).generate()

        folder = published_folder(run_dir, 'msgspec', '0.21.0')
        assert f'INFO | Removing stale wheel: "{folder}"\n' in capture.concat()
        assert not folder.exists()
        assert list(join_path(run_dir, WHEEL_ROOT, 'msgspec').iter_foldernames()) == ['0.22.0']

    def test_name_left_the_provided_set_removed(self, fs, run_dir, mirror):
        """A name that is not provided anymore goes with its folder, the pages included."""
        register_msgspec_wheels(mirror)
        folder = join_path(run_dir, WHEEL_ROOT, 'demo')
        fs.create_file(join_path(folder, '1.0', 'demo-1.0-py3-none-any.whl'), contents=b'stale')
        fs.create_file(join_path(folder, 'index.html'), contents=b'<html></html>')

        with logger.mock_capture_writer() as capture:
            WheelFlow(make_provided_repo(), client=mirror.client).generate()

        assert f'INFO | Removing stale wheel: "{folder}"\n' in capture.concat()
        assert not folder.exists()

    def test_stale_cache_version_removed(self, fs, run_dir, mirror):
        """The cache keeps the versions the two steps need, the others are removed."""
        register_wheels(mirror)
        register_msgspec_wheels(mirror)
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('httpx==0.27.8'),
            'c3': (
                requirement('httpx==0.28.1', f'{PROVIDED_URL}/')
                + requirement('msgspec==0.22.0', f'{PROVIDED_URL}/')
            ),
        }
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        # the lookback versions of the pack channel, and a version out of the window
        for version in ('0.27.7', '0.27.8'):
            fs.create_file(
                join_path(cache_folder(run_dir, 'httpx', version), 'index.html'),
                contents=b'stale')
        fs.create_file(join_path(cache_folder(run_dir, 'httpx', '0.26.0'), 'index.html'),
                       contents=b'stale')
        fs.create_file(join_path(cache_folder(run_dir, 'other', '1.0'), 'index.html'),
                       contents=b'stale')

        gen = WheelFlow(make_repo(requirements, entry=entry), client=mirror.client)
        gen.run()
        # run() fetched the wheels of the window, the stale folders are still there
        assert cache_folder(run_dir, 'httpx', '0.26.0').isdir()
        gen.commit()

        assert not cache_folder(run_dir, 'httpx', '0.26.0').exists()
        assert not join_path(run_dir, CACHE_ROOT, 'other').exists()
        # the versions of both steps are kept: the pack channel of httpx (the
        # lookbacks included) and the provided pin
        for name, version in (
                ('httpx', '0.27.7'), ('httpx', '0.27.8'), ('httpx', '0.28.1'),
                ('msgspec', '0.22.0')):
            assert cache_folder(run_dir, name, version).isdir()

    def test_removal_is_best_effort(self, fs, run_dir, mirror, monkeypatch):
        """A folder that cannot be removed does not fail the flow, the next run retries."""
        register_msgspec_wheels(mirror)
        fs.create_file(join_path(cache_folder(run_dir, 'other', '1.0'), 'index.html'),
                       contents=b'stale')
        fs.create_file(join_path(published_folder(run_dir, 'demo', '1.0'), 'demo-1.0.whl'),
                       contents=b'stale')

        def fail(folder):
            raise PermissionError(f'folder is held: {folder}')

        monkeypatch.setattr(repo_wheel, 'atomic_rmtree', fail)
        with logger.mock_capture_writer() as capture:
            WheelFlow(make_provided_repo(), client=mirror.client).generate()

        log = capture.concat()
        cache = cache_folder(run_dir, 'other', '1.0')
        demo = join_path(run_dir, WHEEL_ROOT, 'demo')
        assert f'WARNING | Failed to remove stale wheel cache "{cache}": ' \
               f'folder is held: {cache}\n' in log
        assert f'WARNING | Failed to remove stale wheel "{demo}": ' \
               f'folder is held: {demo}\n' in log
        assert cache.isdir()
        assert demo.isdir()
