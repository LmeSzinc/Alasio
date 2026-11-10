"""
Tests for the wheels of the provided pins of a repo: repo_wheel.RepoWheel.

The step downloads every wheel of the pins whose index the mod entry declares
in provide_wheel to the private cache, and syncs them to the
folder a client installs from:

    {workspace}/.wheel_cache/{name}/{version}/*.whl   the cache
    {workspace}/wheel/{name}/{version}/*.whl          the pip index

The tests run the step on its own, the way repo.Repo wires it. The mirror is
the MockMirror of the fetch tests (an httpx2.MockTransport handler), the git
repo is a MockGitRepo of a linear chain.
"""
import hashlib
import threading
import time

import pytest

from alasio.deploy.pip_download.pypi_file import WheelHashError, WheelNotFoundError
from alasio.deploy_dev.pack import pack_wheel
from alasio.deploy_dev.pack._pack_cache import PackCache
from alasio.deploy_dev.pack_server import repo_base, repo_wheel
from alasio.deploy_dev.pack_server.repo_base import RepoBase
from alasio.deploy_dev.pack_server.repo_dep import RepoDep
from alasio.deploy_dev.pack_server.repo_wheel import RepoWheel
from alasio.ext.path.atomic import atomic_write
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.conftest import join_path
from tests.deploy_dev.pack_server.test_fetch_wheel import assert_log_unordered
from tests.deploy_dev.pack_server.test_repo import (
    PROVIDED_URL, PROXY, entry_source, make_config, make_proxy_config, make_repo, read_tree, register_wheels,
    requirement
)

# folder of the workspace of the config in the run directory
WORKSPACE = 'pack/Author_Repo_master'

# folder of the private cache of the workspace
CACHE_ROOT = f'{WORKSPACE}/.wheel_cache'

# folder of the wheels a client installs with pip
WHEEL_ROOT = f'{WORKSPACE}/wheel'

# the provided pin of the tests: a compiled dependency, one wheel per platform
MSGS_WHEELS = (
    'msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl',
    'msgspec-0.22.0-cp38-cp38-win_amd64.whl',
)

# a second index the repo provides, for the tests of more than one provided pin
OTHER_URL = 'http://other.example/simple'

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

    def generate(self):
        """Run the step, the whole work of the step."""
        self.run()
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
        PathStr: {run}/pack/Author_Repo_master/.wheel_cache/{name}/{version}
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
        # the two wheels of the version are fetched at once, see
        # WheelFetcher.fetch_async: their lines are the same set every run
        assert_log_unordered(capture, f"""\
INFO | No pack_deps declared by module/config/_index/mod_entry.yaml at c3, no dependency pack is built
INFO | [1/1] Fetching wheels of "msgspec==0.22.0", index="http://provided.example/simple"
INFO | Download index page: "http://provided.example/simple/msgspec/"
INFO | Download wheel: "http://provided.example/packages/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl" -> "{cache}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Download wheel: "http://provided.example/packages/msgspec-0.22.0-cp38-cp38-win_amd64.whl" -> "{cache}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
INFO | [1/1] Publishing wheels of "msgspec==0.22.0"
INFO | Copy wheel: "{cache}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl" -> "{published}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Copy wheel: "{cache}/msgspec-0.22.0-cp38-cp38-win_amd64.whl" -> "{published}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
""")
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
INFO | No pack_deps declared by module/config/_index/mod_entry.yaml at c3, no dependency pack is built
"""
        assert mirror.requests == []
        assert gen.wheel.publish_files == {}
        assert not join_path(run_dir, CACHE_ROOT).exists()
        assert not join_path(run_dir, WHEEL_ROOT).exists()

    def test_pack_channel_is_not_published(self, fs, run_dir, mirror):
        """A version the pack step fetched is not published again as a wheel."""
        register_wheels(mirror)
        mirror.alias(PROVIDED_URL)
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('httpx==0.27.8'),
            'c3': requirement('httpx==0.28.1', f'{PROVIDED_URL}/'),
        }
        # the entry declares httpx as a pack channel and provides its index:
        # the pack serves the version, the wheel of it is not published
        entry = entry_source('httpx', provide=(PROVIDED_URL,))
        base = RepoBase(make_repo(requirements, entry=entry), make_config(), mirror.client)
        RepoDep(base).run()
        requests = list(mirror.requests)

        wheel = RepoWheel(base)
        wheel.run()

        # the pack step already fetched the wheel, the second step asks for none
        assert mirror.requests == requests
        assert wheel.publish_files == {}


class TestProxy:
    """The downloads go through the client of the step, see RepoBase.fetch_client."""

    def test_wheels_through_the_proxy(self, fs, run_dir, mirror, monkeypatch):
        """A step without an injected client fetches with the client the proxy built."""
        register_msgspec_wheels(mirror)
        created = []

        def create_client(proxy):
            created.append(proxy)
            return mirror.client

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        gen = WheelFlow(make_provided_repo(), config=make_proxy_config())

        with logger.mock_capture_writer() as capture:
            gen.generate()

        cache = cache_folder(run_dir, 'msgspec', '0.22.0')
        published = published_folder(run_dir, 'msgspec', '0.22.0')
        assert_log_unordered(capture, f"""\
INFO | No pack_deps declared by module/config/_index/mod_entry.yaml at c3, no dependency pack is built
INFO | Downloading the wheels through the proxy "{PROXY}"
INFO | [1/1] Fetching wheels of "msgspec==0.22.0", index="http://provided.example/simple"
INFO | Download index page: "http://provided.example/simple/msgspec/"
INFO | Download wheel: "http://provided.example/packages/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl" -> "{cache}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Download wheel: "http://provided.example/packages/msgspec-0.22.0-cp38-cp38-win_amd64.whl" -> "{cache}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
INFO | [1/1] Publishing wheels of "msgspec==0.22.0"
INFO | Copy wheel: "{cache}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl" -> "{published}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Copy wheel: "{cache}/msgspec-0.22.0-cp38-cp38-win_amd64.whl" -> "{published}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
""")
        # the wheels are the files of the mirror, fetched by the client the
        # proxy built, and the client is closed with the step
        assert created == [PROXY]
        assert sorted(published.iter_filenames()) == list(MSGS_WHEELS)
        assert mirror.client.is_closed is True

    def test_one_client_of_the_step(self, fs, run_dir, mirror, monkeypatch):
        """Without a proxy the step still fetches with one client of its own."""
        register_msgspec_wheels(mirror)
        created = []

        def create_client(proxy):
            created.append(proxy)
            return mirror.client

        monkeypatch.setattr(repo_base, 'create_client', create_client)
        gen = WheelFlow(make_provided_repo())

        with logger.mock_capture_writer() as capture:
            gen.generate()

        # the client is built once for the whole step, direct, and the two
        # wheels of the version go through it, at once (the order of the two
        # transfers is not part of the contract)
        assert created == ['']
        assert 'Downloading the wheels through the proxy' not in capture.concat()
        page, *wheels = mirror.requests
        assert page == 'http://provided.example/simple/msgspec/'
        assert sorted(wheels) == [
            'http://provided.example/packages/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl',
            'http://provided.example/packages/msgspec-0.22.0-cp38-cp38-win_amd64.whl',
        ]
        assert mirror.client.is_closed is True


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

        assert_log_unordered(capture, f"""\
INFO | No pack_deps declared by module/config/_index/mod_entry.yaml at c3, no dependency pack is built
INFO | [1/1] Fetching wheels of "msgspec==0.22.0", index="http://provided.example/simple"
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'msgspec', '0.22.0')}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Wheel exists, skipped: "{cache_folder(run_dir, 'msgspec', '0.22.0')}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
INFO | [1/1] Publishing wheels of "msgspec==0.22.0"
INFO | Wheel exists, skipped: "{published_folder(run_dir, 'msgspec', '0.22.0')}/msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl"
INFO | Wheel exists, skipped: "{published_folder(run_dir, 'msgspec', '0.22.0')}/msgspec-0.22.0-cp38-cp38-win_amd64.whl"
""")
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

    def test_the_wheels_are_synced_at_once(self, fs, run_dir, mirror, monkeypatch):
        """The files of the run are verified in parallel, a few at a time.

        The work of one file is a read of the disk that usually ends in a skip,
        so a run over an up to date workspace would otherwise read gigabytes one
        after another.
        """
        register_msgspec_wheels(mirror)
        repo = make_provided_repo()
        WheelFlow(repo, client=mirror.client).generate()

        in_flight = []
        peak = []
        lock = threading.Lock()
        original = repo_wheel._is_synced

        def slow(target, sha256):
            with lock:
                in_flight.append(1)
                peak.append(len(in_flight))
            # long enough for the file of the other thread to arrive: the step
            # starts both tasks before either read answers
            time.sleep(0.1)
            try:
                return original(target, sha256)
            finally:
                with lock:
                    in_flight.pop()

        monkeypatch.setattr(repo_wheel, '_is_synced', slow)
        with logger.mock_capture_writer():
            WheelFlow(repo, client=mirror.client).generate()

        # both wheels are verified at the same time, and the worker threads
        # keep the peak under the limit of the step
        assert max(peak) == 2
        assert max(peak) <= repo_wheel.SYNC_CONCURRENT

    def test_a_failed_copy_is_reported_and_the_others_are_written(
            self, fs, run_dir, mirror, monkeypatch):
        """A file that cannot be written names itself and stops no other file."""
        register_msgspec_wheels(mirror)
        repo = make_provided_repo()
        WheelFlow(repo, client=mirror.client).generate()
        failed = published_folder(run_dir, 'msgspec', '0.22.0').joinpath(MSGS_WHEELS[0])
        kept = published_folder(run_dir, 'msgspec', '0.22.0').joinpath(MSGS_WHEELS[1])
        failed.file_remove()
        kept.file_remove()
        original = repo_wheel.atomic_copy

        def copy(source, target, **kwargs):
            if target == failed:
                raise OSError(f'disk is full: {target}')
            original(source, target, **kwargs)

        monkeypatch.setattr(repo_wheel, 'atomic_copy', copy)
        with pytest.raises(OSError) as e:
            WheelFlow(repo, client=mirror.client).generate()

        assert str(failed) in str(e.value)
        assert 'disk is full' in str(e.value)
        # the other file of the run was still written
        assert not failed.exists()
        assert kept.atomic_read_bytes() == cache_folder(
            run_dir, 'msgspec', '0.22.0').joinpath(MSGS_WHEELS[1]).atomic_read_bytes()

    def test_a_pin_is_written_before_the_next_one_starts(self, fs, run_dir, mirror):
        """The line of a pin heads its own writes, not a run of lines to come.

        The wheels of one pin are verified and copied at once, and the next pin
        starts once they are written: a log reads one pin after another instead
        of announcing all of them and then working.
        """
        other = mirror.alias(OTHER_URL)
        register_msgspec_wheels(mirror)
        mirror.register('httpx', '0.28.1')
        requirements = {
            'c3': (requirement('httpx==0.28.1', f'{other}/')
                   + requirement('msgspec==0.22.0', f'{PROVIDED_URL}/')),
        }
        repo = make_repo(requirements, entry=entry_source(provide=(other, PROVIDED_URL)))
        with logger.mock_capture_writer() as capture:
            WheelFlow(repo, client=mirror.client).generate()

        lines = capture.concat().splitlines()
        first = lines.index('INFO | [1/2] Publishing wheels of "httpx==0.28.1"')
        second = lines.index('INFO | [2/2] Publishing wheels of "msgspec==0.22.0"')
        httpx = [index for index, line in enumerate(lines)
                 if line.startswith('INFO | Copy wheel') and 'httpx-0.28.1' in line]
        msgspec = [index for index, line in enumerate(lines)
                   if line.startswith('INFO | Copy wheel') and 'msgspec-0.22.0' in line]
        # the copies of the first pin sit between the two heads, the copies of
        # the second one follow their own head
        assert first < min(httpx) and max(httpx) < second < min(msgspec)

    def test_a_failed_pin_stops_the_pins_after_it(self, fs, run_dir, mirror, monkeypatch):
        """A pin that cannot be written ends the step, the next one is not started."""
        other = mirror.alias(OTHER_URL)
        register_msgspec_wheels(mirror)
        mirror.register('httpx', '0.28.1')
        requirements = {
            'c3': (requirement('httpx==0.28.1', f'{other}/')
                   + requirement('msgspec==0.22.0', f'{PROVIDED_URL}/')),
        }
        repo = make_repo(requirements, entry=entry_source(provide=(other, PROVIDED_URL)))
        original = repo_wheel._sync_wheel

        def sync(source, target, sha256):
            if 'httpx' in target.name:
                raise OSError(f'disk is full: {target}')
            return original(source, target, sha256)

        monkeypatch.setattr(repo_wheel, '_sync_wheel', sync)
        with pytest.raises(OSError) as e:
            WheelFlow(repo, client=mirror.client).generate()

        assert 'disk is full' in str(e.value)
        # the wheels of the second pin are not published at all
        assert not published_folder(run_dir, 'msgspec', '0.22.0').exists()
