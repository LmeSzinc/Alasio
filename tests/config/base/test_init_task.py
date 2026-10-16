"""
Tests for the task lifecycle of a config, see alasio/config/base/config_access.py.

`init_task(task)` binds the config to a task: the groups of the task are loaded
from the config, and the groups of the previous task are released. A template
config has no database, binding a task only sets the task of the config, every
group still reads its default. The template config notice is for the user of the
backend, a test or a dev tool without a backend does not need it.
"""
import pytest

from alasio.backend.worker.bridge import BackendBridge
from alasio.config.alasio.group_proxy import GroupProxy
from alasio.db.conn import SQLITE_POOL
from alasio.logger import logger

# Use a single test config for all tests to reduce file creation
TEST_CONFIG_NAME = ':memory:'
NOTICE = 'Using template config, which is read only'


@pytest.fixture
def backend_inited():
    """
    The state BackendBridge has in a process with a backend, restored afterwards
    """
    backend = BackendBridge()
    previous = backend.inited
    backend.inited = True
    yield backend
    backend.inited = previous


class TestInitTaskBindsTask:
    """
    init_task(task) binds the config to a task: the groups of the task are loaded,
    and the groups of the previous task are released
    """

    def test_binds_the_task_of_a_config_without_a_task(self, config_cls):
        """A config created without a task is bound to a task by init_task(task)."""
        config = config_cls(TEST_CONFIG_NAME, task='')
        assert config.task == ''

        config.init_task('Main')

        assert config.task == 'Main'
        assert type(config.Campaign) is GroupProxy
        assert ('Main', 'Campaign') in config._dict_group

    def test_keeps_the_task_without_an_argument(self, config_cls):
        """init_task() without an argument keeps the task of the config."""
        config = config_cls(TEST_CONFIG_NAME, task='Dashboard')
        config.init_task('Main')

        config.init_task()

        assert config.task == 'Main'
        assert type(config.Campaign) is GroupProxy

    def test_rebinds_another_task(self, config_cls):
        """The groups of the previous task are released on a rebind."""
        config = config_cls(TEST_CONFIG_NAME, task='Main')
        assert type(config.Campaign) is GroupProxy

        config.init_task('Dashboard')

        assert config.task == 'Dashboard'
        # Campaign belongs to Main, it is not bound to Dashboard
        assert type(config.Campaign) is not GroupProxy
        assert ('Main', 'Campaign') not in config._dict_group

    def test_binds_the_same_task_again(self, config_cls):
        """Binding the task the config already has reloads the groups."""
        config = config_cls(TEST_CONFIG_NAME, task='Main')

        config.init_task('Main')

        assert config.task == 'Main'
        assert type(config.Campaign) is GroupProxy

    def test_loads_the_values_of_the_task_it_binds(self, config_cls):
        """The values saved for a task are loaded when it is bound again."""
        config = config_cls(TEST_CONFIG_NAME, task='Main')
        config.Campaign.Name = '7-2'

        config.init_task('Dashboard')
        config.init_task('Main')

        assert config.task == 'Main'
        assert type(config.Campaign) is GroupProxy
        assert config.Campaign.Name == '7-2'


class TestInitTaskTemplateConfig:
    """
    A template config is read only and has no database: binding a task sets the
    task of the config, and every group still reads its default
    """

    def test_binds_the_task_of_a_template_config(self, config_cls):
        config = config_cls('template')
        assert config.task == ''

        config.init_task('Main')

        assert config.task == 'Main'
        assert config.is_template_config is True

    def test_reads_defaults_without_a_database(self, config_cls):
        # Pools left by the tests before this one are not the ones under test
        before = set(SQLITE_POOL.all_pool)

        config = config_cls('template')
        config.init_task('Main')

        assert len(config._dict_row) == 0
        assert len(config._dict_group) == 0
        # Default of Campaign.Name in the example mod, see main_model.py
        assert config.Campaign.Name == '12-4'
        # No database was opened, the group above read its default in memory
        assert set(SQLITE_POOL.all_pool) == before


class TestTemplateConfigNotice:
    """
    The notice of a template config is logged when a backend is inited, the user
    of the backend is the one it is written for
    """

    def test_without_a_backend(self, config_cls):
        with logger.mock_capture_writer() as capture:
            config_cls('template')

        assert capture.concat() == ''

    def test_with_a_backend(self, config_cls, backend_inited):
        with logger.mock_capture_writer() as capture:
            config_cls('template')

        assert capture.concat() == f'INFO | {NOTICE}\n'
