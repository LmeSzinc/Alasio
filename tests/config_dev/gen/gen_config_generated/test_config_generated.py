"""
Tests of config_generated.py (GenConfigGenerated.generate_config_generated_file).

The file is a stub struct that gives the IDE the type of every group of the
user config, so the runtime never reads it. A group is annotated once, the
second binding is emitted as a comment, and a Scheduler variant is commented
out because the Scheduler attribute is declared by the base class.
"""
import pytest

from alasio.config_dev.gen.gen_config_generated import GenConfigGenerated
from alasio.config_dev.parse.base import DefinitionError
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy, make_cross

NAVS = {
    'main': """\
SchedulerDaily:
  args:
    Enable: true
Campaign:
  args:
    Name:
      value: main
      option: [main, event]
""",
    'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name:
      value: gems
      option: [gems, event]
""",
}

TASKS = {
    'main': """\
Farming:
  groups:
    SchedulerDaily:
    Campaign:
""",
    'gems': """\
GemsFarming:
  groups:
    GemsCampaign:
""",
}


def build(fs, navs=NAVS, tasks=TASKS):
    """
    Build a GenConfigGenerated with the nav order of the fixture.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        navs (dict[str, str]): key: nav name, value of {nav}.args.yaml
        tasks (dict[str, str]): key: nav name, value of {nav}.tasks.yaml

    Returns:
        GenConfigGenerated:
    """
    return make_cross(fs, navs, tasks, GenConfigGenerated)


def generated(gen):
    """
    Generate config_generated.py and return its content.

    Args:
        gen (GenConfigGenerated): Generator under test

    Returns:
        str: Content of the generated file
    """
    gen.generate_config_generated_file()
    return gen.path_config.joinpath('_index/config_generated.py').atomic_read_text()


class TestConfigGenerated:
    def test_file_content(self, fs):
        """Every group without args gets an annotation."""
        gen = build(fs)
        assert generated(gen) == """\
import typing as t

from alasio.config.base import AlasioConfigBase

from .mod_entry import MOD_ENTRY

if t.TYPE_CHECKING:
    from ..gems import gems_model as gems
    from ..main import main_model as main


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class ConfigGenerated(AlasioConfigBase):
    \"\"\"
    A generated config struct to fool IDE's type-predict and auto-complete
    \"\"\"
    entry = MOD_ENTRY

    \"\"\"
    ========== nav: gems ==========
    \"\"\"
    # ----- GemsFarming -----
    GemsCampaign: "gems.GemsCampaign"

    \"\"\"
    ========== nav: main ==========
    \"\"\"
    # ----- Farming -----
    SchedulerDaily: "main.SchedulerDaily"
    Campaign: "main.Campaign"
"""

    def test_scheduler_group_is_commented(self, fs):
        """A group inheriting Scheduler only gets a comment."""
        gen = build(fs, navs={'main': """\
Scheduler:
  args:
    Enable: true
SchedulerDaily:
  parent: Scheduler
  args:
    Hour: 0
"""}, tasks={'main': """\
Task:
  groups:
    SchedulerDaily:
"""})
        code = generated(gen)
        assert '# SchedulerDaily: "main.SchedulerDaily"' in code
        assert '\n    SchedulerDaily:' not in code

    def test_missing_nav_warns(self, fs):
        """Without any nav the file is not generated."""
        gen = build(fs, navs={'main': 'Group:\n  args:\n    X: 1\n'}, tasks={'main': ''})
        gen.dict_nav_config = {}
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate_config_generated_file(gitadd=gitadd)
        assert capture.concat() == 'WARNING | No navigation configs found for config_generated.py\n'
        assert gitadd.files == []

    def test_type_checking_imports_are_sorted(self, fs):
        """The lazy model imports are sorted by nav name.

        The navs of dict_nav_config come from the folders of the mod, so their
        order is the order of the filesystem. The imports must not change when
        a nav folder is added or removed elsewhere, while the class body keeps
        the order of nav.order.yaml.
        """
        gen = build(fs)
        code = generated(gen)
        imports = [line.strip() for line in code.splitlines() if line.startswith('    from ..')]
        assert imports == [
            'from ..gems import gems_model as gems',
            'from ..main import main_model as main',
        ]
        # the class body is still ordered by nav.order.yaml, gems first
        assert code.index('========== nav: gems') < code.index('========== nav: main')

    def test_group_without_args_is_skipped(self, fs):
        """An inforef group has no model, it is not annotated."""
        gen = build(fs, navs={'main': """\
Info:

Group:
  args:
    X: 1
"""}, tasks={'main': """\
Task:
  groups:
    Group:
  displays:
    - [{info: Info}, Group]
"""})
        code = generated(gen)
        assert 'Group: "main.Group"' in code
        assert 'Info:' not in code

    def test_group_bound_twice_is_commented(self, fs):
        """The second model of one group name only adds a comment.

        The second model is a variant of the first one, so the declared type
        still describes it and the binding is not reported.
        """
        gen = build(fs, navs={'main': """\
Base:
  args:
    X: 1
Other:
  parent: Base
  args:
    X: 2
Group:
  parent: Base
  args:
    X: 3
"""}, tasks={'main': """\
First:
  groups:
    Group: Base
Second:
  groups:
    Group: Other
"""})
        with logger.mock_capture_writer() as capture:
            code = generated(gen)
        assert code.count('    Group: "main.Base"') == 1
        assert '# Group: "main.Other"' in code
        assert 'Multiple validation model bound' not in capture.concat()

    def test_group_bound_to_two_models_warns(self, fs):
        """A group name validated by two unrelated models is reported."""
        gen = build(fs, navs={'main': """\
Base:
  args:
    X: 1
Other:
  args:
    Y: 2
Group:
  parent: Base
  args:
    X: 3
"""}, tasks={'main': """\
First:
  groups:
    Group: Base
Second:
  groups:
    Group: Other
"""})
        # the second generation writes nothing, so the log holds the warning only
        gen.generate_config_generated_file()
        with logger.mock_capture_writer() as capture:
            gen.generate_config_generated_file()
        assert capture.concat() == (
            'WARNING | Multiple validation model bound on group Second.Group, '
            "might cause unexpected behaviour, models: ['main.Base', 'main.Other']\n"
        )

    def test_group_bound_to_the_same_model_is_silent(self, fs):
        """The same group bound by several tasks validates with one model."""
        gen = build(fs, navs={'main': """\
Group:
  args:
    X: 1
"""}, tasks={'main': """\
First:
  groups:
    Group:
Second:
  groups:
    Group:
"""})
        with logger.mock_capture_writer() as capture:
            gen.generate_config_generated_file()
        assert 'Multiple validation model bound' not in capture.concat()

    def test_conflict_warns_once_per_model(self, fs):
        """A model already reported is not reported again by the next task."""
        gen = build(fs, navs={'main': """\
Base:
  args:
    X: 1
Other:
  args:
    Y: 2
Group:
  parent: Base
  args:
    X: 3
"""}, tasks={'main': """\
First:
  groups:
    Group: Base
Second:
  groups:
    Group: Other
Third:
  groups:
    Group: Other
"""})
        gen.generate_config_generated_file()
        with logger.mock_capture_writer() as capture:
            gen.generate_config_generated_file()
        assert capture.concat() == (
            'WARNING | Multiple validation model bound on group Second.Group, '
            "might cause unexpected behaviour, models: ['main.Base', 'main.Other']\n"
        )

    def test_group_without_parser_is_reported(self, fs):
        """A group not built by a nav parser is reported instead of crashing."""
        gen = build(fs)
        group = gen.groups_data['Campaign']
        del group.__dict__['parser']
        with pytest.raises(DefinitionError) as e:
            gen.generate_config_generated_file()
        assert e.value.msg == 'Group "Campaign" does not have a parser'
        assert e.value.keys == ['Farming', 'groups']
