"""
Tests of the {nav}_i18n.json generation, organized by the generated file.

The i18n file keeps the manual translations of the previous generation, and
fills the missing ones with the default text. A translation that is identical
to the default is written inline, so an untranslated file stays compact.
"""

import json

import pytest

from alasio.config_dev.parse.load_alas_i18n import LoadAlasI18n
from alasio.ext.file.jsonfile import NoIndent, json_dumps_custom_indent
from alasio.ext.path.calc import joinpath
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy, make_alasio_generator, make_generator


def i18n_text(data):
    """
    Serialize i18n data exactly like generate() writes it.

    Args:
        data (dict): Value of ConfigGenerator.i18n_data

    Returns:
        str:
    """
    return json_dumps_custom_indent(data).decode('utf-8')


class TestI18nStructure:
    def test_default_name_and_help(self, fs):
        """A missing translation defaults to {group}.{arg} and an empty help."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""")
        assert i18n_text(gen.i18n_data) == """\
{
  "Group": {
    "_info": {
      "zh-CN": {"name": "Group", "help": ""},
      "en-US": {"name": "Group", "help": ""}
    },
    "Mode": {
      "zh-CN": {"name": "Group.Mode", "help": ""},
      "en-US": {"name": "Group.Mode", "help": ""}
    }
  }
}"""

    def test_languages_follow_entry_order(self, fs):
        """The languages of each arg are written in gui_language order."""
        gen = make_generator(
            fs, yaml="Group:\n  args:\n    Mode: normal\n", languages=('en-US', 'zh-CN'))
        assert list(gen.i18n_data['Group']['_info']) == ['en-US', 'zh-CN']
        assert list(gen.i18n_data['Group']['Mode']) == ['en-US', 'zh-CN']

    def test_hidden_arg_is_skipped(self, fs):
        """A hidden arg is not translated, it is not shown on the frontend."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Shown: 1
    Hidden:
      dt: static-hide
      value: x
""")
        assert gen.i18n_data == {
            'Group': {
                '_info': {
                    'zh-CN': {'name': 'Group', 'help': ''},
                    'en-US': {'name': 'Group', 'help': ''},
                },
                'Shown': {
                    'zh-CN': {'name': 'Group.Shown', 'help': ''},
                    'en-US': {'name': 'Group.Shown', 'help': ''},
                },
            },
        }
        assert list(gen.i18n_data['Group']) == ['_info', 'Shown']

    def test_empty_group_has_info_only(self, fs):
        """An inforef group without args only has its _info."""
        gen = make_generator(fs, yaml="""\
Info:
""")
        assert gen.i18n_data == {
            'Info': {
                '_info': {
                    'zh-CN': {'name': 'Info', 'help': ''},
                    'en-US': {'name': 'Info', 'help': ''},
                },
            },
        }

    def test_variant_has_override_args_only(self, fs):
        """A variant translates the args it overrides, the others are inherited."""
        gen = make_generator(fs, yaml="""\
Base:
  args:
    Mode: normal
    Count: 1

Variant:
  parent: Base
  args:
    Count: 2
""")
        assert list(gen.i18n_data) == ['Base', 'Variant']
        assert list(gen.i18n_data['Variant']) == ['Count']
        assert gen.i18n_data['Variant']['Count']['zh-CN'] == {
            'name': 'Variant.Count', 'help': '',
        }


class TestI18nVariantInfo:
    def test_variant_without_override_i18n_has_no_info(self, fs):
        """A variant reuses the _info of its ancestor."""
        gen = make_generator(fs, yaml="""\
Base:
  args:
    Mode: normal

Variant:
  parent: Base
  args:
    Mode: hard
""")
        assert '_info' not in gen.i18n_data['Variant']
        assert '_info' in gen.i18n_data['Base']

    def test_variant_with_override_i18n_has_info(self, fs):
        """override_i18n gives the variant an _info of its own."""
        gen = make_generator(fs, yaml="""\
Base:
  args:
    Mode: normal

Variant:
  parent: Base
  override_i18n: true
  args:
    Mode: hard
""")
        assert gen.i18n_data['Variant']['_info'] == {
            'zh-CN': {'name': 'Variant', 'help': ''},
            'en-US': {'name': 'Variant', 'help': ''},
        }

    def test_dashboard_group_has_info(self, fs):
        """A dashboard group always gets an _info of its own."""
        gen = make_generator(fs, yaml="""\
Oil:
  parent: DashboardTotal
  args:
    Value: 0
""")
        gen.groups_data['Oil'].dashboard = 'Total'
        assert gen.i18n_data['Oil']['_info'] == {
            'zh-CN': {'name': 'Oil', 'help': ''},
            'en-US': {'name': 'Oil', 'help': ''},
        }

    def test_mixin_nav_has_no_i18n(self, fs):
        """The mixin nav only holds classes that are not shown on the frontend."""
        gen = make_alasio_generator(fs, nav='mixin', yaml="""\
Future12hMixin:
  args:
    NextRun:
      dt: datetime
      value: 2020-01-01 00:00:00
""")
        assert gen.i18n_data == {}


class TestI18nOption:
    def test_select_options(self, fs):
        """The translation of each option defaults to the option itself."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode:
      value: normal
      option: [normal, hard]
""")
        assert gen.i18n_data['Group']['Mode']['zh-CN']['option_i18n'] == {
            'normal': 'normal',
            'hard': 'hard',
        }

    def test_numeric_options_are_inline(self, fs):
        """Untranslated options that are digits are written inline."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Level:
      value: 2
      option: [1, 2, 3]
""")
        assert type(gen.i18n_data['Group']['Level']['zh-CN']['option_i18n']) is NoIndent
        assert i18n_text(gen.i18n_data) == """\
{
  "Group": {
    "_info": {
      "zh-CN": {"name": "Group", "help": ""},
      "en-US": {"name": "Group", "help": ""}
    },
    "Level": {
      "zh-CN": {
        "name": "Group.Level",
        "help": "",
        "option_i18n": {"1": "1", "2": "2", "3": "3"}
      },
      "en-US": {
        "name": "Group.Level",
        "help": "",
        "option_i18n": {"1": "1", "2": "2", "3": "3"}
      }
    }
  }
}"""

    def test_translated_numeric_option_is_not_inline(self, fs):
        """A translated option takes one line per option."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Level:
      value: 2
      option: [1, 2, 3]
""", i18n={
            'Group': {'Level': {'zh-CN': {'option_i18n': {'1': 'One'}}}},
        }, languages=('zh-CN',))
        assert type(gen.i18n_data['Group']['Level']['zh-CN']['option_i18n']) is not NoIndent
        assert gen.i18n_data['Group']['Level']['zh-CN']['option_i18n'] == {
            '1': 'One',
            '2': '2',
            '3': '3',
        }

    def test_option_dict_groups_and_options(self, fs):
        """A secondary-select translates its groups and its options."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Table:
      dt: secondary-select
      value: 1-1
      option_dict:
        chapter1: [1-1, 1-2]
""", languages=('zh-CN',))
        assert gen.i18n_data['Group']['Table']['zh-CN']['option_i18n'] == {
            'chapter1': 'chapter1',
            '1-1': '1-1',
            '1-2': '1-2',
        }
        assert type(gen.i18n_data['Group']['Table']['zh-CN']['option_i18n']) is not NoIndent

    def test_translated_option_equal_to_default_is_inline(self, fs):
        """A translation that equals the option is not a translation."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Level:
      value: 2
      option: [1, 2, 3]
""", i18n={
            'Group': {'Level': {'zh-CN': {'option_i18n': {'1': '1'}}}},
        }, languages=('zh-CN',))
        assert type(gen.i18n_data['Group']['Level']['zh-CN']['option_i18n']) is NoIndent

    def test_option_dict_group_translation(self, fs):
        """The translated name of an option_dict group is kept."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Table:
      dt: secondary-select
      value: 1-1
      option_dict:
        chapter1: [1-1, 1-2]
""", i18n={
            'Group': {'Table': {'zh-CN': {'option_i18n': {'chapter1': 'Chapter 1'}}}},
        }, languages=('zh-CN',))
        assert gen.i18n_data['Group']['Table']['zh-CN']['option_i18n'] == {
            'chapter1': 'Chapter 1',
            '1-1': '1-1',
            '1-2': '1-2',
        }

    def test_enable_options(self, fs):
        """enable has the "true" and "false" options."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Enable:
      dt: enable
      value: false
""", languages=('zh-CN',))
        assert gen.i18n_data['Group']['Enable']['zh-CN']['option_i18n'] == {
            'true': 'true',
            'false': 'false',
        }

    def test_static_option(self, fs):
        """A static arg has its value as the only option."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode:
      dt: static
      value: enabled
""", languages=('zh-CN',))
        assert gen.i18n_data['Group']['Mode']['zh-CN']['option_i18n'] == {
            'enabled': 'enabled',
        }

    def test_help_breaks_the_inline_layout(self, fs):
        """An arg with help is written one line per field."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""", i18n={
            'Group': {'Mode': {'zh-CN': {'help': 'Some help'}}},
        }, languages=('zh-CN',))
        data = gen.i18n_data['Group']['Mode']['zh-CN']
        assert type(data) is not NoIndent
        assert data == {'name': 'Group.Mode', 'help': 'Some help'}


class TestI18nOld:
    def test_old_name_and_help_are_kept(self, fs):
        """The manual translations survive a regeneration."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""", i18n={
            'Group': {
                '_info': {'zh-CN': {'name': 'The group', 'help': 'Group help'}},
                'Mode': {'zh-CN': {'name': 'The mode', 'help': 'Mode help'}},
            },
        }, languages=('zh-CN',))
        assert i18n_text(gen.i18n_data) == """\
{
  "Group": {
    "_info": {
      "zh-CN": {
        "name": "The group",
        "help": "Group help"
      }
    },
    "Mode": {
      "zh-CN": {
        "name": "The mode",
        "help": "Mode help"
      }
    }
  }
}"""

    def test_old_multiline_help_is_split(self, fs):
        """A multi-line help is stored as a list of lines."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""", i18n={
            'Group': {
                '_info': {'zh-CN': {'help': 'line1\nline2\n'}},
                'Mode': {'zh-CN': {'help': ['a\nb', 'c']}},
            },
        }, languages=('zh-CN',))
        assert gen.i18n_data['Group']['_info']['zh-CN']['help'] == ['line1', 'line2']
        assert gen.i18n_data['Group']['Mode']['zh-CN']['help'] == ['a', 'b', 'c']

    def test_old_multiline_arg_name_is_split(self, fs):
        """A multi-line arg name is stored as a list of lines."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""", i18n={
            'Group': {'Mode': {'zh-CN': {'name': 'line1\nline2'}}},
        }, languages=('zh-CN',))
        assert gen.i18n_data['Group']['Mode']['zh-CN']['name'] == ['line1', 'line2']

    def test_old_entry_of_removed_arg_is_dropped(self, fs):
        """The generator maintains the structure of the i18n file."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""", i18n={
            'RemovedGroup': {'RemovedArg': {'zh-CN': {'name': 'x'}}},
            'Group': {'RemovedArg': {'zh-CN': {'name': 'y'}}},
        }, languages=('zh-CN',))
        assert list(gen.i18n_data) == ['Group']
        assert list(gen.i18n_data['Group']) == ['_info', 'Mode']

    def test_old_string_option_translation(self, fs):
        """A translated option of a select is kept."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode:
      value: normal
      option: [normal, hard]
""", i18n={
            'Group': {'Mode': {'zh-CN': {'option_i18n': {'normal': 'NORMAL'}}}},
        }, languages=('zh-CN',))
        assert gen.i18n_data['Group']['Mode']['zh-CN']['option_i18n'] == {
            'normal': 'NORMAL',
            'hard': 'hard',
        }
        assert type(gen.i18n_data['Group']['Mode']['zh-CN']['option_i18n']) is not NoIndent

    def test_old_option_of_removed_option_is_dropped(self, fs):
        """The translations of the options no longer defined are dropped."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode:
      value: normal
      option: [normal, hard]
""", i18n={
            'Group': {'Mode': {'zh-CN': {'option_i18n': {'normal': 'NORMAL', 'legacy': 'LEGACY'}}}},
        }, languages=('zh-CN',))
        assert gen.i18n_data['Group']['Mode']['zh-CN']['option_i18n'] == {
            'normal': 'NORMAL',
            'hard': 'hard',
        }


class TestI18nAlas:
    """
    The translations of the old ALAS i18n format, registered to LoadAlasI18n.
    """

    def make_loader(self, fs, data):
        """
        Register an old {lang}.json folder on LoadAlasI18n.

        Args:
            fs (FakeFilesystem): The in-memory filesystem fixture
            data (dict): Content of the old zh-CN.json
        """
        folder = joinpath(fs.root_dir.path, 'old_i18n')
        fs.create_file(joinpath(folder, 'zh-CN.json'), contents=json.dumps(data, ensure_ascii=False))
        LoadAlasI18n().register(folder)

    def test_option_translation_is_loaded(self, fs):
        """The old translation of a string option is migrated."""
        self.make_loader(fs, {'OldGroup': {'Mode': {'normal': 'NORMAL', 'hard': 'HARD'}}})
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode:
      value: normal
      option: [normal, hard]
""", languages=('zh-CN',))
        LoadAlasI18n().redirect_group('OldGroup', 'Group')
        assert gen.i18n_data['Group']['Mode']['zh-CN']['option_i18n'] == {
            'normal': 'NORMAL',
            'hard': 'HARD',
        }

    def test_numeric_option_translation_is_loaded(self, fs):
        """The old translation of a numeric option is migrated.

        The key of the old json is always a str, while the option of the yaml
        may be a number, so the lookup converts the option to a str.
        """
        self.make_loader(fs, {'Group': {'Level': {'1': 'One', '2': 'Two'}}})
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Level:
      value: 2
      option: [1, 2, 3]
""", languages=('zh-CN',))
        option_i18n = gen.i18n_data['Group']['Level']['zh-CN']['option_i18n']
        assert type(option_i18n) is not NoIndent
        assert option_i18n == {
            '1': 'One',
            '2': 'Two',
            '3': '3',
        }
        assert i18n_text(gen.i18n_data) == """\
{
  "Group": {
    "_info": {
      "zh-CN": {"name": "Group", "help": ""}
    },
    "Level": {
      "zh-CN": {
        "name": "Group.Level",
        "help": "",
        "option_i18n": {
          "1": "One",
          "2": "Two",
          "3": "3"
        }
      }
    }
  }
}"""

    def test_name_and_help_are_loaded(self, fs):
        """The old name and help are migrated."""
        self.make_loader(fs, {'Group': {'Mode': {'name': 'The mode', 'help': 'Mode help'}}})
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""", languages=('zh-CN',))
        assert gen.i18n_data['Group']['Mode']['zh-CN'] == {
            'name': 'The mode',
            'help': 'Mode help',
        }

    def test_default_like_name_is_ignored(self, fs):
        """An old name like {group}.{arg} is not a translation."""
        self.make_loader(fs, {'Group': {'Mode': {'name': 'Other.Name'}}})
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""", languages=('zh-CN',))
        assert gen.i18n_data['Group']['Mode']['zh-CN']['name'] == 'Group.Mode'

    def test_arg_redirect(self, fs):
        """The translations of a renamed arg are migrated."""
        self.make_loader(fs, {'OldGroup': {'OldMode': {'name': 'The mode'}}})
        LoadAlasI18n().redirect_arg('OldGroup.OldMode', 'Group.Mode')
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""", languages=('zh-CN',))
        assert gen.i18n_data['Group']['Mode']['zh-CN']['name'] == 'The mode'


class TestI18nFile:
    def test_generate_writes_i18n_file(self, fs):
        """generate() writes the i18n file next to the args yaml."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""", languages=('zh-CN',))
        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert gen.i18n_file.atomic_read_text() == """\
{
  "Group": {
    "_info": {
      "zh-CN": {"name": "Group", "help": ""}
    },
    "Mode": {
      "zh-CN": {"name": "Group.Mode", "help": ""}
    }
  }
}"""
        assert capture.fd.any_contains(f'Write file {gen.i18n_file}')

    def test_unchanged_i18n_file_is_not_written(self, fs):
        """The second generation of the same yaml writes nothing."""
        make_generator(fs, yaml="Group:\n  args:\n    Mode: normal\n").generate()
        gitadd = GitAddSpy()
        gen = make_generator(fs, yaml=None)
        with logger.mock_capture_writer() as capture:
            gen.generate(gitadd=gitadd)
        assert not capture.fd.any_contains('Write file')
        assert gitadd.files == []

    def test_no_i18n_file_without_groups(self, fs):
        """No i18n file is created when there is nothing to translate."""
        gen = make_generator(fs, yaml="")
        gen.generate()
        assert not gen.i18n_file.exists()

    @pytest.mark.xfail(strict=True, reason='a stale i18n file is left behind when all groups are removed')
    def test_stale_i18n_file_is_removed(self, fs):
        """The i18n of a nav without group is removed."""
        gen = make_generator(
            fs, yaml="", i18n={'Removed': {'_info': {'zh-CN': {'name': 'x'}}}})
        gen.generate()
        assert not gen.i18n_file.exists()

    @pytest.mark.xfail(strict=True, reason='a multi-line group name is converted with str(), the file contains a python list repr')
    def test_multiline_group_name_is_formatted(self, fs):
        """A multi-line group name is written as a list of lines."""
        gen = make_generator(
            fs, yaml="Info:\n",
            i18n={'Info': {'_info': {'zh-CN': {'name': ['line1', 'line2']}}}},
            languages=('zh-CN',))
        assert gen.i18n_data['Info']['_info']['zh-CN']['name'] == ['line1', 'line2']
