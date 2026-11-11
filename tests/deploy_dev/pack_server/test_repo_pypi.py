"""
Tests for the pages of the pip index of a repo: repo_pypi.RepoPypi.

The step writes three kinds of page on top of the wheels repo_wheel publishes:

    {workspace}/wheel/{name}/index.html   the simple page of a distribution
    {workspace}/wheel/index.html          the find-links page of the folder
    {workspace}/wheel/flat.html           the page a person reads

The tests run the two steps of the flow on their own, the way repo.Repo wires
them: the wheels are the input of the pages, and the git repo is a MockGitRepo
of a linear chain. The mirror is the MockMirror of the fetch tests.
"""

import re

import pytest

from alasio.deploy_dev.pack_server.repo_base import RepoBase
from alasio.deploy_dev.pack_server.repo_pypi import FLAT_PAGE, RepoPypi
from alasio.deploy_dev.pack_server.repo_wheel import RepoWheel
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.conftest import join_path
from tests.deploy_dev.pack_server.test_repo import (
    PROVIDED_URL, Repo as RepoFlow, entry_source, make_config, make_repo, register_wheels, requirement, run_commit
)

pytestmark = [
    pytest.mark.parallel,
]

# folder of the workspace of the config in the run directory
WORKSPACE = 'pack/Author_Repo_master'

# folder of the pages and of the wheels a client installs with pip
WHEEL_ROOT = f'{WORKSPACE}/wheel'

# the url the folder is served at, the command of the flat page of the tests
FIND_LINKS_URL = 'https://packs.example/pack/Author_Repo_master/wheel'

# the wheels of the provided pins of the tests: a compiled extension of two
# platforms, and a wheel of pure python
MSGS_WHEELS = (
    'msgspec-0.22.0-cp38-cp38-manylinux_2_17_x86_64.whl',
    'msgspec-0.22.0-cp38-cp38-win_amd64.whl',
)
HTTPX_WHEEL = 'httpx-0.28.1-py3-none-any.whl'

# the dependency files of the tests: the latest commit provides the index of
# the two dependencies, the older commits pin the versions of the window
REQUIREMENTS = {
    'c1': requirement('httpx==0.27.7'),
    'c2': requirement('httpx==0.27.8'),
    'c3': (
        requirement('httpx==0.28.1', f'{PROVIDED_URL}/')
        + requirement('msgspec==0.22.0', f'{PROVIDED_URL}/')
    ),
}


def make_provided_repo(requirements=None):
    """
    Build the repo of the provided pins of the tests.

    Args:
        requirements (dict, optional): {commit: dependency file}. Defaults to
            None, REQUIREMENTS

    Returns:
        MockGitRepo: Repo of the tests
    """
    return make_repo(
        requirements if requirements is not None else REQUIREMENTS,
        entry=entry_source(provide=(PROVIDED_URL,)))


def register_provided_wheels(mirror):
    """
    Register the wheels of the provided pins in the mock mirror.

    Args:
        mirror (MockMirror): Mirror to register the wheels in
    """
    mirror.alias(PROVIDED_URL)
    register_wheels(mirror)
    for tags in ('cp38-cp38-manylinux_2_17_x86_64', 'cp38-cp38-win_amd64'):
        mirror.register('msgspec', '0.22.0', tags=tags)


def page_file(run_dir, *parts):
    """
    Path of a page of the pip index of the workspace.

    Returns:
        PathStr: {run}/pack/Author_Repo_master/wheel/{parts}
    """
    return join_path(run_dir, WHEEL_ROOT, *parts)


class PypiFlow:
    """
    The pages of the provided wheels of a repo, for the tests.

    The flow of repo.Repo runs RepoWheel and then RepoPypi; the tests of this
    module run the two steps on their own, in the same order.
    """

    def __init__(self, repo, config=None, client=None, find_links_url=None):
        """
        Args:
            repo (MockGitRepo): Git repo to publish
            config (PackRepoModel, optional): Config of the repo. Defaults to
                None, the config of the tests
            client (httpx2.AsyncClient, optional): Client of the mock mirror
            find_links_url (str, optional): Url of the folder for the command
                of the flat page
        """
        self.base = RepoBase(repo, config if config is not None else make_config(), client)
        self.wheel = RepoWheel(self.base)
        self.pypi = RepoPypi(self.base, self.wheel, find_links_url=find_links_url)

    def run(self):
        """Publish the wheels of the provided pins, then write their pages."""
        self.wheel.run()
        self.pypi.run()
        return self


class TestPages:
    """The pages a run of the provided pins writes."""

    def test_three_pages(self, fs, run_dir, mirror):
        """A simple page a name, the find-links page of the folder, the flat page."""
        register_provided_wheels(mirror)
        gen = PypiFlow(make_provided_repo(), client=mirror.client).run()

        assert gen.pypi.pages == [
            page_file(run_dir, 'httpx', 'index.html'),
            page_file(run_dir, 'msgspec', 'index.html'),
            page_file(run_dir, 'index.html'),
            page_file(run_dir, 'flat.html'),
        ]
        for file in gen.pypi.pages:
            assert file.isfile()

    def test_run_log(self, fs, run_dir, mirror):
        """A rerun of a page that did not change writes nothing."""
        register_provided_wheels(mirror)
        repo = make_provided_repo()
        gen = PypiFlow(repo, client=mirror.client).run()

        with logger.mock_capture_writer() as capture:
            gen.pypi.run()

        assert capture.concat() == f"""\
INFO | Publishing the pip index pages of "Author_Repo_master": httpx, msgspec
INFO | Page exists, skipped: "{page_file(run_dir, 'httpx', 'index.html')}"
INFO | Page exists, skipped: "{page_file(run_dir, 'msgspec', 'index.html')}"
INFO | Page exists, skipped: "{page_file(run_dir, 'index.html')}"
INFO | Page exists, skipped: "{page_file(run_dir, 'flat.html')}"
"""

    def test_no_provided_pin(self, fs, run_dir, mirror):
        """A repo that provides no index writes no page, and no folder."""
        register_wheels(mirror)
        gen = PypiFlow(make_repo(), client=mirror.client)

        with logger.mock_capture_writer() as capture:
            gen.run()

        assert capture.concat().endswith(
            'INFO | No provided dependency of "Author_Repo_master", no pip index page is written\n')
        assert gen.pypi.pages == []
        assert not join_path(run_dir, WHEEL_ROOT).exists()

    def test_page_of_a_rerun_is_not_rewritten(self, fs, run_dir, mirror):
        """A page of the same wheels keeps its mtime, the host serves what it cached."""
        register_provided_wheels(mirror)
        repo = make_provided_repo()
        gen = PypiFlow(repo, client=mirror.client).run()
        stats = [file.stat().st_mtime_ns for file in gen.pypi.pages]

        gen.pypi.run()

        assert [file.stat().st_mtime_ns for file in gen.pypi.pages] == stats


class TestPageOfAName:
    """The simple page of one distribution, the shape pip reads as an index."""

    def test_page(self, fs, run_dir, mirror):
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'httpx', 'index.html').atomic_read_text()
        assert page.startswith("""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for httpx</title>
  </head>
  <body>
    <h1>Links for httpx</h1>
""")
        # the page sits in the folder of the distribution: a link starts at the
        # version, and carries the digest pip verifies
        assert page.count('<a href="0.28.1/') == 1
        assert HTTPX_WHEEL in page
        assert page.count('#sha256=') == 1

    def test_page_of_a_compiled_dependency(self, fs, run_dir, mirror):
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'msgspec', 'index.html').atomic_read_text()
        assert '<title>Links for msgspec</title>' in page
        for filename in MSGS_WHEELS:
            assert f'<a href="0.22.0/{filename}#sha256=' in page

    def test_latest_only(self, fs, run_dir, mirror):
        """Only the pin of the latest commit is on the page, not the lookbacks."""
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'httpx', 'index.html').atomic_read_text()
        assert '0.28.1/' in page
        assert '0.27.8/' not in page
        assert '0.27.7/' not in page


class TestFindLinksPage:
    """The page of the whole folder, the shape pip reads with --find-links."""

    def test_page(self, fs, run_dir, mirror):
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'index.html').atomic_read_text()
        assert page.startswith("""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Wheels of Author_Repo_master</title>
  </head>
  <body>
    <h1>Wheels of Author_Repo_master</h1>
""")
        # the page sits above the folders of the distributions: a link starts
        # at the name
        assert f'<a href="httpx/0.28.1/{HTTPX_WHEEL}#sha256=' in page
        for filename in MSGS_WHEELS:
            assert f'<a href="msgspec/0.22.0/{filename}#sha256=' in page
        assert page.count('<a href=') == 3

    def test_not_a_pep503_page(self, fs, run_dir, mirror):
        """The find-links page of a folder declares no simple api version."""
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()
        assert 'pypi:repository-version' not in page_file(run_dir, 'index.html').atomic_read_text()


class TestFlatPage:
    """The page a person opens."""

    def test_page(self, fs, run_dir, mirror):
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'flat.html').atomic_read_text()
        assert '<title>Wheels of Author_Repo_master</title>' in page
        assert '<nav id="contents" aria-label="Contents">' in page
        # the index links every distribution and every version of it
        assert '<a href="#httpx">httpx</a> <span class="badge pure">pure python</span>' in page
        assert '<a href="#msgspec">msgspec</a> <span class="badge compiled">compiled</span>' in page
        # a wheel of pure python is the table of its versions
        assert '<table class="matrix pure">' in page
        assert HTTPX_WHEEL in page
        # a compiled extension is a matrix, one column per python tag
        assert '<span class="plat">win_amd64</span>' in page
        assert '<span class="plat">manylinux_2_17_x86_64</span>' in page

    def test_links_of_the_flat_page(self, fs, run_dir, mirror):
        """Every link of the flat page is the relative path of the wheel."""
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'flat.html').atomic_read_text()
        assert f'href="httpx/0.28.1/{HTTPX_WHEEL}#sha256=' in page
        for filename in MSGS_WHEELS:
            assert f'href="msgspec/0.22.0/{filename}#sha256=' in page

    def test_command_with_the_url(self, fs, run_dir, mirror):
        register_provided_wheels(mirror)
        PypiFlow(
            make_provided_repo(), client=mirror.client, find_links_url=FIND_LINKS_URL).run()

        page = page_file(run_dir, 'flat.html').atomic_read_text()
        assert (
            f'      <pre><code>pip install --no-index --find-links {FIND_LINKS_URL} '
            f'&lt;package&gt;</code></pre>\n') in page

    def test_no_command_without_the_url(self, fs, run_dir, mirror):
        """A run that does not know the url writes no command, not a broken one."""
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'flat.html').atomic_read_text()
        assert '<pre>' not in page
        assert 'pip install' not in page

    def test_page_is_not_on_a_pep503_path(self, fs, run_dir, mirror):
        """The flat page names no simple api and sits next to the find-links page."""
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'flat.html').atomic_read_text()
        assert 'pypi:repository-version' not in page
        assert not page_file(run_dir, 'msgspec', 'flat.html').exists()


class TestFlow:
    """The pages of the flow of repo.Repo."""

    def test_pages_written_by_commit(self, fs, run_dir, mirror):
        """The pages of the index belong to the publication: a run writes them at commit."""
        register_provided_wheels(mirror)
        gen = RepoFlow(make_provided_repo(), make_config(), client=mirror.client)
        with logger.mock_capture_writer():
            gen.run()

        assert not page_file(run_dir, 'index.html').exists()
        assert not page_file(run_dir, 'httpx', 'index.html').exists()

        with logger.mock_capture_writer():
            gen.commit()

        assert page_file(run_dir, 'index.html').isfile()
        assert page_file(run_dir, 'flat.html').isfile()
        assert page_file(run_dir, 'httpx', 'index.html').isfile()
        assert page_file(run_dir, 'msgspec', 'index.html').isfile()

    def test_failed_run_keeps_the_index_of_the_previous_version(self, fs, run_dir, mirror):
        """A run that fails keeps serving the version latest.pack names, index included."""
        register_provided_wheels(mirror)
        with logger.mock_capture_writer():
            run_commit(make_provided_repo(), make_config(), mirror.client)
        index = page_file(run_dir, 'msgspec', 'index.html')
        before = index.atomic_read_text()
        assert '0.22.0/' in before

        # the next version publishes another version of msgspec, and the tree
        # packs of that run fail before the publication
        mirror.register('msgspec', '0.23.0', tags='cp38-cp38-win_amd64')
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('httpx==0.27.8'),
            'c3': (
                requirement('httpx==0.28.1')
                + requirement('msgspec==0.22.0', f'{PROVIDED_URL}/')),
            'c4': (
                requirement('httpx==0.28.1')
                + requirement('msgspec==0.23.0', f'{PROVIDED_URL}/')),
        }
        gen = RepoFlow(make_provided_repo(requirements), make_config(), client=mirror.client)

        def fail():
            raise RuntimeError('the tree packs failed')

        gen.pack.run = fail
        with logger.mock_capture_writer():
            with pytest.raises(RuntimeError, match='the tree packs failed'):
                gen.run()

        # the wheels of the new version are on the disk, the index still lists
        # the wheels of the version latest.pack names
        assert join_path(run_dir, WHEEL_ROOT, 'msgspec', '0.23.0').isdir()
        assert index.atomic_read_text() == before

    def test_pages_kept_by_the_cleanup(self, fs, run_dir, mirror):
        """The pages are in the manifest of the run, the cleanup keeps them."""
        register_provided_wheels(mirror)
        with logger.mock_capture_writer():
            run_commit(make_provided_repo(), make_config(), mirror.client)
        assert page_file(run_dir, 'flat.html').isfile()
        assert page_file(run_dir, 'httpx', 'index.html').isfile()

    def test_name_leaving_the_set_takes_its_folder(self, fs, run_dir, mirror):
        """A name the next version does not provide loses its wheels and its page."""
        register_provided_wheels(mirror)
        with logger.mock_capture_writer():
            run_commit(make_provided_repo(), make_config(), mirror.client)
        assert page_file(run_dir, 'msgspec', 'index.html').isfile()

        # the next version of the repo provides httpx only
        requirements = {
            'c1': requirement('httpx==0.27.7'),
            'c2': requirement('httpx==0.27.8'),
            'c3': requirement('httpx==0.28.1', f'{PROVIDED_URL}/'),
        }
        with logger.mock_capture_writer() as capture:
            run_commit(make_provided_repo(requirements), make_config(), mirror.client)

        assert not join_path(run_dir, WHEEL_ROOT, 'msgspec').exists()
        assert page_file(run_dir, 'httpx', 'index.html').isfile()
        # the page of the folder no longer links the name
        page = page_file(run_dir, 'index.html').atomic_read_text()
        assert 'msgspec' not in page
        assert 'httpx/0.28.1/' in page
        assert (f'INFO | Removing stale file: '
                f'"{page_file(run_dir, "msgspec", "index.html")}"\n') in capture.concat()

    def test_no_page_after_the_set_is_empty(self, fs, run_dir, mirror):
        """A version that provides nothing leaves no page of the previous one."""
        register_provided_wheels(mirror)
        with logger.mock_capture_writer():
            run_commit(make_provided_repo(), make_config(), mirror.client)
        assert page_file(run_dir, 'flat.html').isfile()

        with logger.mock_capture_writer():
            run_commit(make_repo(), make_config(), mirror.client)

        assert not join_path(run_dir, WHEEL_ROOT).exists()


class TestWheelLinkOfTheRun:
    """The links of the pages are the files the run published."""

    def test_every_published_file_is_linked(self, fs, run_dir, mirror):
        register_provided_wheels(mirror)
        gen = PypiFlow(make_provided_repo(), client=mirror.client).run()

        published = sorted(
            wheel.file.name
            for wheels in gen.wheel.publish_files.values()
            for wheel in wheels)
        page = page_file(run_dir, 'index.html').atomic_read_text()
        assert sorted(
            filename for filename in published if filename in page) == published

    def test_digest_is_the_digest_of_the_file(self, fs, run_dir, mirror):
        """The fragment of a link is the sha256 of the wheel the run downloaded."""
        register_provided_wheels(mirror)
        gen = PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'index.html').atomic_read_text()
        for (name, version), wheels in gen.wheel.publish_files.items():
            for wheel in wheels:
                assert (f'{name}/{version}/{wheel.file.name}#sha256={wheel.hashes["sha256"]}') in page


class TestPageLayout:
    """
    The paths and the links of the pages: the shape a client may rely on.

    A client of the pack server never asks a host to turn a directory into an
    `index.html`: it asks the file. So the page of a distribution is at
    `{name}/index.html` and every link inside a page is relative, resolving
    under the folder of the page; a page that pointed at an absolute url, or
    walked up with `..`, would leave the folder the static host serves.
    """

    def test_pages_are_files(self, fs, run_dir, mirror):
        """Every page the run writes is a file, and it is an index.html or the flat page."""
        register_provided_wheels(mirror)
        gen = PypiFlow(make_provided_repo(), client=mirror.client).run()

        for file in gen.pypi.pages:
            assert file.isfile()
            assert file.name in ('index.html', FLAT_PAGE)

    def test_per_name_page_is_name_index_html(self, fs, run_dir, mirror):
        """The page of a distribution is the index.html of its folder, name normalized."""
        register_provided_wheels(mirror)
        gen = PypiFlow(make_provided_repo(), client=mirror.client).run()

        for (name, _version) in gen.wheel.publish_files:
            file = page_file(run_dir, name, 'index.html')
            assert file in gen.pypi.pages
            # the name of the folder is the normalized one, the same the client
            # builds its url from
            assert file.uppath().name == name

    @pytest.mark.parametrize('file_of', ['index.html', 'flat.html'])
    def test_links_of_the_folder_page(self, fs, run_dir, mirror, file_of):
        """A link of the folder page starts at the name and stays below the folder."""
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, file_of).atomic_read_text()
        # the anchors of the page itself (#name, #name-version) are not links
        # to a file, the rest of them are
        hrefs = [href for href in _hrefs(page) if not href.startswith('#')]
        assert hrefs
        for href in hrefs:
            assert not _is_absolute(href), href
            assert '..' not in href, href
            assert href.split('/', 1)[0] in ('httpx', 'msgspec'), href

    def test_links_of_a_per_name_page(self, fs, run_dir, mirror):
        """A link of the page of a distribution starts at the version of it."""
        register_provided_wheels(mirror)
        PypiFlow(make_provided_repo(), client=mirror.client).run()

        page = page_file(run_dir, 'msgspec', 'index.html').atomic_read_text()
        hrefs = [href for href in _hrefs(page) if not href.startswith('#')]
        assert hrefs
        for href in hrefs:
            assert not _is_absolute(href), href
            assert '..' not in href, href
            # {version}/{file}, resolved against {name}/index.html, lands in
            # {name}/{version}/{file} of the static host
            assert href.split('/')[0] == '0.22.0', href

    def test_no_page_of_the_folder_is_a_directory(self, fs, run_dir, mirror):
        """The run writes no page at a path a host would have to map to a file."""
        register_provided_wheels(mirror)
        gen = PypiFlow(make_provided_repo(), client=mirror.client).run()
        for file in gen.pypi.pages:
            assert file.suffix == '.html'
            assert not str(file).endswith('/')


def _hrefs(page):
    """
    The hrefs of a page, escaped entities left as they are.

    Args:
        page (str): Text of the page

    Returns:
        list[str]: The values of the href attributes, in the order of the page
    """
    return re.findall(r'href="([^"]*)"', page)


def _is_absolute(href):
    """
    Whether a link names its own scheme or host, so it leaves the folder.

    Args:
        href (str): Value of an href attribute

    Returns:
        bool: True for a scheme ('https:'), a host ('//host') or a root ('/')
    """
    return ':' in href.split('/', 1)[0] or href.startswith(('/', '//'))
