import pytest

from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen.gen_task_entry import GenTaskEntry
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.concurrent.threadpool import Job
from alasio.ext.path.atomic import atomic_read_text
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401


def make_gen(fs, folders=None):
    """
    Create a GenTaskEntry on the fake filesystem

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        folders (list[str], optional): task_entry_folders override

    Returns:
        GenTaskEntry:
    """
    root = fs.root_dir.path.rstrip('/\\')
    entry = ModEntryInfo(
        name='test',
        root=root,
        path_config='module/config',
    )
    if folders is not None:
        entry.task_entry_folders = dict.fromkeys(folders)
    return GenTaskEntry(entry)


def write_code(fs, path, code):
    """
    Write a python source file on the fake filesystem

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        path (str): Absolute path
        code (str): Python code text
    """
    root = fs.root_dir.path.rstrip('/\\')
    fs.create_file(f'{root}/{path}', contents=code)


CODE_REWARD = """
from alasio.base.scheduler.task_entry import alasio_task


class Reward:
    @alasio_task('Reward')
    def run(self):
        ...
"""

# a MOD that overrides the scheduler tasks
CODE_RESTART = """
from alasio.base.scheduler.task_entry import alasio_task


class Restart:
    @alasio_task('RestartDevice')
    def restart_device(self):
        ...

    @alasio_task('RestartGame')
    def restart_game(self):
        ...
"""


class TestIterTaskEntryFiles:
    """Tests for _iter_task_entry_files()."""

    def test_scan_configured_folders_only(self, fs):
        """Only folders in task_entry_folders are scanned."""
        write_code(fs, 'module/reward.py', CODE_REWARD)
        write_code(fs, 'tasks/shop.py', CODE_REWARD.replace("'Reward'", "'Shop'").replace('Reward', 'Shop'))
        write_code(fs, 'scripts/other.py', CODE_REWARD)
        gen = make_gen(fs)
        files = sorted(str(f) for f in gen._iter_task_entry_files())
        assert len(files) == 2
        assert all('scripts' not in f for f in files)
        assert any(f.endswith('module/reward.py') for f in files)
        assert any(f.endswith('tasks/shop.py') for f in files)

    def test_skip_underscore_segments(self, fs):
        """Path segments starting with "_" are skipped."""
        write_code(fs, 'module/reward.py', CODE_REWARD)
        write_code(fs, 'module/_index/task_entry.py', CODE_REWARD)
        write_code(fs, 'module/__pycache__/reward.cpython-38.py', CODE_REWARD)
        gen = make_gen(fs)
        files = list(gen._iter_task_entry_files())
        assert len(files) == 1
        assert str(files[0]).endswith('module/reward.py')

    def test_missing_folder_silently_skipped(self, fs):
        """A configured folder that does not exist is silently skipped."""
        write_code(fs, 'module/reward.py', CODE_REWARD)
        gen = make_gen(fs, folders=['module', 'nope'])
        files = list(gen._iter_task_entry_files())
        assert len(files) == 1
        assert str(files[0]).endswith('module/reward.py')


class TestScanTaskEntry:
    """Tests for scan_task_entry()."""

    def test_aggregate_across_files(self, fs):
        """Entries from different files are aggregated by task name."""
        write_code(fs, 'module/reward/reward.py', CODE_REWARD)
        write_code(fs, 'tasks/shop.py', """
class Shop:
    @alasio_task('Shop')
    def run(self):
        ...
""")
        gen = make_gen(fs)
        data = gen.scan_task_entry()
        assert sorted(data) == ['Reward', 'Shop']
        assert data['Reward'].cls == 'Reward'
        assert data['Reward'].func == 'run'
        assert data['Reward'].file == 'module/reward/reward.py'
        assert data['Shop'].file == 'tasks/shop.py'

    def test_duplicate_task_name_raises(self, fs):
        """Duplicate task names raise DefinitionError."""
        write_code(fs, 'module/reward.py', CODE_REWARD)
        write_code(fs, 'tasks/reward2.py', CODE_REWARD.replace('class Reward', 'class Reward2'))
        gen = make_gen(fs)
        with pytest.raises(DefinitionError) as e:
            gen.scan_task_entry()
        assert 'Duplicate task entry: "Reward"' in str(e.value)
        assert e.value.file == 'tasks/reward2.py'

    def test_files_without_marker_skipped(self, fs):
        """Files without the marker are skipped."""
        write_code(fs, 'module/reward.py', """
class Reward:
    def run(self):
        ...
""")
        gen = make_gen(fs)
        assert gen.scan_task_entry() == {}

    def test_multiple_decorators_one_method(self, fs):
        """Multiple decorators on one method generate multiple task entries."""
        write_code(fs, 'module/reward.py', """
class Reward:
    @alasio_task('Reward')
    @alasio_task('Shop')
    def run(self):
        ...
""")
        gen = make_gen(fs)
        data = gen.scan_task_entry()
        assert sorted(data) == ['Reward', 'Shop']
        assert data['Reward'].func == 'run'
        assert data['Shop'].func == 'run'

    def test_duplicate_decorator_task_name_raises(self, fs):
        """The same task name on multiple decorators of one method is a duplicate."""
        write_code(fs, 'module/reward.py', """
class Reward:
    @alasio_task('Reward')
    @alasio_task('Reward')
    def run(self):
        ...
""")
        gen = make_gen(fs)
        with pytest.raises(DefinitionError) as e:
            gen.scan_task_entry()
        assert 'Duplicate task entry: "Reward"' in str(e.value)


class TestStartScanBackground:
    """Tests for start_task_entry_scan() and task_entry_data."""

    def test_start_scan_submits_single_job(self, fs):
        """start_task_entry_scan() submits a background job without blocking."""
        write_code(fs, 'module/reward.py', CODE_REWARD)
        gen = make_gen(fs)
        gen.start_task_entry_scan()
        assert isinstance(gen._task_entry_job, Job)

    def test_task_entry_data_returns_scan_result(self, fs):
        """task_entry_data() waits for the job and returns the aggregated result."""
        write_code(fs, 'module/reward.py', CODE_REWARD)
        write_code(fs, 'tasks/shop.py', """
class Shop:
    @alasio_task('Shop')
    def run(self):
        ...
""")
        gen = make_gen(fs)
        gen.start_task_entry_scan()
        data = gen.task_entry_data
        assert sorted(data) == ['Reward', 'Shop']


class TestGenerateFile:
    """Tests for generate_task_entry_file()."""

    def test_generate_with_entries(self, fs):
        """The generated file contains one method per task entry."""
        write_code(fs, 'module/reward/reward.py', CODE_REWARD)
        write_code(fs, 'tasks/shop.py', """
class Shop:
    @alasio_task('Shop')
    def run(self):
        ...
""")
        gen = make_gen(fs)
        gen.start_task_entry_scan()
        gen.generate_task_entry_file()
        content = atomic_read_text(gen.task_entry_file)
        # no scheduler task is overridden, so there is no divider for normal tasks
        assert content == '''\
from alasio.base.scheduler.scheduler import AlasioScheduler


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class TaskEntryGenerated(AlasioScheduler):
    """
    Task entry functions, generated from @alasio_task() markers
    """

    def Reward(self):
        from module.reward.reward import Reward
        Reward(config=self.config, device=self.device).run()

    def Shop(self):
        from tasks.shop import Shop
        Shop(config=self.config, device=self.device).run()
'''

    def test_generate_multiple_decorators(self, fs):
        """Multiple decorators on one method generate one entry method per task."""
        write_code(fs, 'module/reward.py', """
class Reward:
    @alasio_task('Reward')
    @alasio_task('Shop')
    def run(self):
        ...
""")
        gen = make_gen(fs)
        gen.start_task_entry_scan()
        gen.generate_task_entry_file()
        content = atomic_read_text(gen.task_entry_file)
        assert content == '''\
from alasio.base.scheduler.scheduler import AlasioScheduler


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class TaskEntryGenerated(AlasioScheduler):
    """
    Task entry functions, generated from @alasio_task() markers
    """

    def Reward(self):
        from module.reward import Reward
        Reward(config=self.config, device=self.device).run()

    def Shop(self):
        from module.reward import Reward
        Reward(config=self.config, device=self.device).run()
'''

    def test_generate_alasio_comment(self, fs):
        """alasio itself is generated with the gen_alasio codegen entry comment."""
        entry = ModEntryInfo.alasio()
        gen = GenTaskEntry(entry)
        gen.start_task_entry_scan()
        gen.generate_task_entry_file()
        content = atomic_read_text(gen.task_entry_file)
        assert content == '''\
from alasio.base.scheduler.scheduler import AlasioScheduler


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config_dev.gen_alasio ```

class TaskEntryGenerated(AlasioScheduler):
    """
    Task entry functions, generated from @alasio_task() markers
    """
    pass
'''

    def test_generate_empty(self, fs):
        """The file is still generated with an empty class when nothing is found."""
        write_code(fs, 'module/plain.py', """
class Plain:
    def run(self):
        ...
""")
        gen = make_gen(fs)
        gen.start_task_entry_scan()
        gen.generate_task_entry_file()
        content = atomic_read_text(gen.task_entry_file)
        assert content == '''\
from alasio.base.scheduler.scheduler import AlasioScheduler


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class TaskEntryGenerated(AlasioScheduler):
    """
    Task entry functions, generated from @alasio_task() markers
    """
    pass
'''

    def test_generate_scheduler_task_first(self, fs):
        """Scheduler tasks are generated first and sorted on their own."""
        write_code(fs, 'module/restart.py', CODE_RESTART)
        write_code(fs, 'module/reward.py', CODE_REWARD)
        gen = make_gen(fs)
        gen.start_task_entry_scan()
        gen.generate_task_entry_file()
        content = atomic_read_text(gen.task_entry_file)
        assert content == '''\
from alasio.base.scheduler.scheduler import AlasioScheduler


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class TaskEntryGenerated(AlasioScheduler):
    """
    Task entry functions, generated from @alasio_task() markers
    """

    def RestartDevice(self):
        from module.restart import Restart
        Restart(config=self.config, device=self.device).restart_device()

    def RestartGame(self):
        from module.restart import Restart
        Restart(config=self.config, device=self.device).restart_game()

    """
    ========== normal tasks ==========
    """

    def Reward(self):
        from module.reward import Reward
        Reward(config=self.config, device=self.device).run()
'''

    def test_generate_scheduler_task_only(self, fs):
        """No divider when the MOD does not have normal tasks."""
        write_code(fs, 'module/restart.py', CODE_RESTART)
        gen = make_gen(fs)
        gen.start_task_entry_scan()
        gen.generate_task_entry_file()
        content = atomic_read_text(gen.task_entry_file)
        assert content == '''\
from alasio.base.scheduler.scheduler import AlasioScheduler


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class TaskEntryGenerated(AlasioScheduler):
    """
    Task entry functions, generated from @alasio_task() markers
    """

    def RestartDevice(self):
        from module.restart import Restart
        Restart(config=self.config, device=self.device).restart_device()

    def RestartGame(self):
        from module.restart import Restart
        Restart(config=self.config, device=self.device).restart_game()
'''


class TestSchedulerTaskNames:
    """Tests for scheduler_task_names."""

    def test_scheduler_task_names(self, fs):
        """Scheduler task names come from SchedulerTask, sorted."""
        gen = make_gen(fs)
        assert gen.scheduler_task_names == [
            'GotoMain',
            'RestartDevice',
            'RestartGame',
            'StopDevice',
            'StopGame',
        ]


class TestCheckSchedulerTask:
    """Tests for check_scheduler_task()."""

    def test_warn_missing_scheduler_task(self, fs):
        """A warning is printed for every scheduler task that is not overridden."""
        write_code(fs, 'module/reward.py', CODE_REWARD)
        gen = make_gen(fs)
        data = gen.scan_task_entry()
        with logger.mock_capture_writer() as capture:
            gen.check_scheduler_task(data)
        assert capture.fd.any_contains(
            'Scheduler task not overridden by task entry: GotoMain, RestartDevice, RestartGame, StopDevice, StopGame'
        )
        assert capture.fd.any_contains('NotImplementedError')

    def test_warn_partial_override(self, fs):
        """Only the scheduler tasks that are not overridden are warned."""
        write_code(fs, 'module/restart.py', CODE_RESTART)
        gen = make_gen(fs)
        data = gen.scan_task_entry()
        with logger.mock_capture_writer() as capture:
            gen.check_scheduler_task(data)
        assert capture.fd.any_contains(
            'Scheduler task not overridden by task entry: GotoMain, StopDevice, StopGame'
        )

    def test_no_warn_when_all_overridden(self, fs):
        """No warning when the MOD overrides all scheduler tasks."""
        write_code(fs, 'module/restart.py', CODE_RESTART)
        write_code(fs, 'module/main.py', """
class Main:
    @alasio_task('GotoMain')
    @alasio_task('StopDevice')
    @alasio_task('StopGame')
    def run(self):
        ...
""")
        gen = make_gen(fs)
        data = gen.scan_task_entry()
        with logger.mock_capture_writer() as capture:
            gen.check_scheduler_task(data)
        assert len(capture.fd.logs) == 0

    def test_no_warn_for_alasio(self, fs):
        """alasio has no MOD scheduler, so it does not override any scheduler task."""
        entry = ModEntryInfo.alasio()
        gen = GenTaskEntry(entry)
        gen.start_task_entry_scan()
        with logger.mock_capture_writer() as capture:
            gen.generate_task_entry_file()
        assert not capture.fd.any_contains('Scheduler task not overridden')
