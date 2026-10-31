"""
Tests of nav.order.yaml (GenNavIndex.nav_order_data, generate_nav_order).

The file records the order of the navs of the navigation component: the order
already in the file is kept, a new nav is appended in alphabetical order, and a
nav that no longer has a task is dropped.
"""
from alasio.config_dev.gen.gen_nav_index import GenNavIndex
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy, make_cross

NAVS = {
    'main': 'MainGroup:\n  args:\n    X: 1\n',
    'gems': 'GemsGroup:\n  args:\n    X: 1\n',
    'opsi': 'OpsiGroup:\n  args:\n    X: 1\n',
}

TASKS = {
    'main': 'Main:\n  groups:\n    MainGroup:\n',
    'gems': 'Gems:\n  groups:\n    GemsGroup:\n',
    'opsi': 'Opsi:\n  groups:\n    OpsiGroup:\n',
}


def build(fs, order=None, navs=NAVS, tasks=TASKS):
    """
    Build a GenNavIndex, optionally with an existing nav.order.yaml.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        order (str, optional): Content of nav.order.yaml
        navs (dict[str, str]): key: nav name, value of {nav}.args.yaml
        tasks (dict[str, str]): key: nav name, value of {nav}.tasks.yaml

    Returns:
        GenNavIndex:
    """
    gen = make_cross(fs, navs, tasks, GenNavIndex)
    if order is not None:
        fs.create_file(gen.nav_order_file, contents=order)
    return gen


class TestNavOrderData:
    def test_new_navs_are_sorted(self, fs):
        """Without a file the navs are appended in alphabetical order."""
        gen = build(fs)
        assert list(gen.nav_order_data) == ['gems', 'main', 'opsi']

    def test_existing_order_is_kept(self, fs):
        """The order of the existing file wins, a new nav is appended last."""
        gen = build(fs, order='opsi:\nmain:\n')
        assert list(gen.nav_order_data) == ['opsi', 'main', 'gems']

    def test_removed_nav_is_dropped(self, fs):
        """A nav that no longer exists is removed from the order."""
        gen = build(fs, order='removed:\nmain:\n', navs={'main': NAVS['main']},
                    tasks={'main': TASKS['main']})
        assert list(gen.nav_order_data) == ['main']

    def test_nav_without_task_is_skipped(self, fs):
        """A nav without task is not part of the navigation."""
        gen = build(fs, navs={'main': NAVS['main'], 'empty': 'EmptyGroup:\n  args:\n    X: 1\n'},
                    tasks={'main': TASKS['main']})
        assert list(gen.nav_order_data) == ['main']

    def test_dashboard_is_skipped(self, fs):
        """The dashboard nav is not listed, it is a fixed component."""
        gen = build(fs, navs={'main': NAVS['main'], 'dashboard': 'Oil:\n  args:\n    Value: 0\n'},
                    tasks={'main': TASKS['main'], 'dashboard': 'OilTask:\n  groups:\n    Oil:\n'})
        assert list(gen.nav_order_data) == ['main']


class TestGenerateNavOrder:
    def test_writes_the_file(self, fs):
        """The order is written and staged, every nav is a null key."""
        gen = build(fs)
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_nav_order(gitadd=gitadd)
        assert capture.concat() == f'INFO | Write file {gen.nav_order_file}\n'
        assert gitadd.files == [str(gen.nav_order_file)]
        assert gen.nav_order_file.atomic_read_text() == """\
gems: null

main: null

opsi: null
"""

    def test_second_run_writes_nothing(self, fs):
        """The same order does not rewrite the file."""
        gen = build(fs)
        gen.generate_nav_order()
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_nav_order(gitadd=gitadd)
        assert capture.concat() == ''
        assert gitadd.files == []

    def test_deletes_an_orphan_file(self, fs):
        """A file whose navs have no task left is deleted and staged."""
        gen = build(fs, order='removed:\nmain:\n', tasks={'main': ''})
        assert gen.nav_order_data == {}
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_nav_order(gitadd=gitadd)
        assert capture.concat() == f'INFO | Delete file {gen.nav_order_file}\n'
        assert gitadd.files == [str(gen.nav_order_file)]
        assert not gen.nav_order_file.exists()
