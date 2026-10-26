"""
Tests of ParseBase.

ParseBase owns the file naming contract of a nav ({nav}.args.yaml ->
{nav}.tasks.yaml / {nav}_i18n.json / {nav}_config.json / {nav}_model.py) and
the yaml/json readers with their postprocess hooks. The postprocess registry
itself is covered by test_postprocess.py, here the readers are tested with a
real file on the fake filesystem.
"""

import textwrap

import pytest

import alasio.config_dev.parse.postprocess as postprocess_module
from alasio.config_dev.parse.base import DefinitionError, ParseBase, args_yaml_postprocess, i18n_json_postprocess
from alasio.config_dev.parse.postprocess import PostprocessInfo
from alasio.ext.path import PathStr
from alasio.ext.path.calc import joinpath
from alasio.testing.filesystem import fs  # noqa: F401


def process_missing_class(cls, data):
    return data


# A class body qualname that passes registration, but the class is not in the
# module globals when the postprocess runs.
process_missing_class.__qualname__ = 'MissingClass.process'


def make_base(fs, nav='main', args_yaml=None, tasks_yaml=None, i18n_json=None):
    """
    Create a ParseBase of a nav on the fake filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        nav (str): Nav name
        args_yaml (str): Content of {nav}.args.yaml, None to leave it missing
        tasks_yaml (str): Content of {nav}.tasks.yaml, None to leave it missing
        i18n_json (str): Content of {nav}_i18n.json, None to leave it missing

    Returns:
        ParseBase: Parser of the nav
    """
    root = fs.root_dir.path
    file = joinpath(root, f'module/config/{nav}/{nav}.args.yaml')
    if args_yaml is not None:
        fs.create_file(file, contents=textwrap.dedent(args_yaml))
    parser = ParseBase(PathStr.new(file))
    if tasks_yaml is not None:
        fs.create_file(parser.tasks_file, contents=textwrap.dedent(tasks_yaml))
    if i18n_json is not None:
        fs.create_file(parser.i18n_file, contents=textwrap.dedent(i18n_json))
    return parser


class TestParseBaseInit:
    def test_file_and_nav_name(self):
        file = PathStr.new('/project/module/config/main/main.args.yaml')
        parser = ParseBase(file)
        assert parser.file == file
        assert parser.nav_name == 'main'

    def test_derived_files(self):
        parser = ParseBase(PathStr.new('/project/module/config/main/main.args.yaml'))
        assert parser.tasks_file == '/project/module/config/main/main.tasks.yaml'
        assert parser.i18n_file == '/project/module/config/main/main_i18n.json'
        assert parser.config_file == '/project/module/config/main/main_config.json'
        assert parser.model_file == '/project/module/config/main/main_model.py'

    @pytest.mark.parametrize('file', [
        '/project/module/config/main/main.yaml',
        '/project/module/config/main/main_args.yaml',
        '/project/module/config/main/main.args.yaml.bak',
        '/project/module/config/main/main.args.yml',
    ])
    def test_invalid_suffix(self, file):
        with pytest.raises(DefinitionError) as e:
            ParseBase(PathStr.new(file))
        assert e.value.msg == 'Arg file must endswith ".args.yaml"'
        assert e.value.file == file

    @pytest.mark.parametrize('file', [
        '/project/module/config/main/Main.args.yaml',
        '/project/module/config/main/main_1.args.yaml',
        '/project/module/config/main/main-1.args.yaml',
        '/project/module/config/main/1main.args.yaml',
        '/project/module/config/main/.args.yaml',
    ])
    def test_invalid_nav_name(self, file):
        with pytest.raises(DefinitionError) as e:
            ParseBase(PathStr.new(file))
        nav = PathStr.new(file).name[: -len('.args.yaml')]
        assert e.value.msg == f'Invalid nav name: "{nav}"'
        assert e.value.file == file

    @pytest.mark.parametrize('nav', ['main', 'nav2', 'a1', 'abcdefghijklmnopqrstuvwxyz'])
    def test_valid_nav_name(self, nav):
        parser = ParseBase(PathStr.new(f'/project/module/config/{nav}/{nav}.args.yaml'))
        assert parser.nav_name == nav


class TestReadArgsYaml:
    def test_missing_file(self, fs):
        parser = make_base(fs, 'main')
        assert parser.read_args_yaml() == {}

    def test_valid_yaml(self, fs):
        parser = make_base(fs, 'main', args_yaml="""\
Group:
  args:
    Enable: true
""")
        assert parser.read_args_yaml() == {'Group': {'args': {'Enable': True}}}

    def test_empty_file(self, fs):
        """An empty yaml document is an empty mapping for generator inputs."""
        parser = make_base(fs, 'main', args_yaml='')
        assert parser.read_args_yaml() == {}

    def test_malformed_yaml(self, fs):
        """Malformed yaml is an error, not an empty mapping."""
        parser = make_base(fs, 'main', args_yaml='Group: [\n')
        with pytest.raises(DefinitionError) as e:
            parser.read_args_yaml()
        assert e.value.msg.startswith('Invalid yaml: ')
        assert e.value.file == parser.file

    def test_yaml_root_is_list(self, fs):
        parser = make_base(fs, 'main', args_yaml='- Group\n- Other\n')
        with pytest.raises(DefinitionError) as e:
            parser.read_args_yaml()
        assert e.value.msg == 'Expected a mapping at yaml root, got list'
        assert e.value.file == parser.file

    def test_yaml_root_is_scalar(self, fs):
        parser = make_base(fs, 'main', args_yaml='Group\n')
        with pytest.raises(DefinitionError) as e:
            parser.read_args_yaml()
        assert e.value.msg == 'Expected a mapping at yaml root, got str'
        assert e.value.file == parser.file

    def test_postprocess_applied(self, fs, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        calls = []

        @args_yaml_postprocess('main')
        def add_group(data):
            calls.append('add_group')
            data['Injected'] = {'args': {'A': 1}}
            return data

        parser = make_base(fs, 'main', args_yaml="""\
Group:
  args:
    A: 1
""")
        assert parser.read_args_yaml() == {
            'Group': {'args': {'A': 1}},
            'Injected': {'args': {'A': 1}},
        }
        assert calls == ['add_group']

    def test_postprocess_chain_order(self, fs, monkeypatch):
        """The output of a postprocess is the input of the next one."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @args_yaml_postprocess('main')
        def first(data):
            data['A'] = 1
            return data

        @args_yaml_postprocess('main')
        def second(data):
            data['B'] = data['A'] + 1
            return data

        parser = make_base(fs, 'main', args_yaml='{}\n')
        assert parser.read_args_yaml() == {'A': 1, 'B': 2}

    def test_registered_plan_is_used(self, fs, monkeypatch):
        """The call plan is built at registration, runtime does not inspect again."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @args_yaml_postprocess('main')
        def process(data):
            data['A'] = 1
            return data

        def fail(_):
            raise AssertionError('_get_postprocess_plan must not be called at runtime')

        monkeypatch.setattr(postprocess_module, '_get_postprocess_plan', fail)
        parser = make_base(fs, 'main', args_yaml='{}\n')
        assert parser.read_args_yaml() == {'A': 1}

    def test_postprocess_returning_none(self, fs, monkeypatch):
        """A postprocess returning None is a config error, the chain stops."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        calls = []

        @args_yaml_postprocess('main')
        def first(data):
            calls.append('first')
            return None

        @args_yaml_postprocess('main')
        def second(data):
            calls.append('second')
            return data

        parser = make_base(fs, 'main', args_yaml='{}\n')
        with pytest.raises(DefinitionError) as e:
            parser.read_args_yaml()
        assert e.value.msg == (
            'Postprocess function "first" returned None, '
            'did you forget "return data"?'
        )
        assert e.value.file == parser.file
        assert calls == ['first']

    def test_protected_nav_skips_postprocess(self, fs, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        calls = []

        @args_yaml_postprocess('alasio')
        def process(data):
            calls.append('process')
            return {'injected': True}

        parser = make_base(fs, 'alasio', args_yaml="""\
Group:
  args:
    A: 1
""")
        assert parser.read_args_yaml() == {'Group': {'args': {'A': 1}}}
        assert calls == []

    def test_no_postprocess_keeps_data(self, fs, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        parser = make_base(fs, 'main', args_yaml="""\
Group:
  args:
    A: 1
""")
        assert parser.read_args_yaml() == {'Group': {'args': {'A': 1}}}

    def test_postprocess_error_gets_file(self, fs, monkeypatch):
        """A postprocess class resolution error points at the args yaml."""
        info = PostprocessInfo.from_function(process_missing_class)
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {
            'main': [info],
        })
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        parser = make_base(fs, 'main', args_yaml='{}\n')
        with pytest.raises(DefinitionError) as e:
            parser.read_args_yaml()
        assert e.value.msg == (
            'Cannot resolve the class of postprocess function '
            '"process_missing_class" from __qualname__ '
            '"MissingClass.process", define it in a class body '
            'or use a 1-arg (data) function'
        )
        assert e.value.file == parser.file


class TestReadTasksYaml:
    def test_missing_file(self, fs):
        parser = make_base(fs, 'main')
        assert parser.read_tasks_yaml() == {}

    def test_valid_yaml(self, fs):
        parser = make_base(fs, 'main', tasks_yaml="""\
Task:
  groups:
    Group:
""")
        assert parser.read_tasks_yaml() == {'Task': {'groups': {'Group': None}}}

    def test_empty_file(self, fs):
        parser = make_base(fs, 'main', tasks_yaml='')
        assert parser.read_tasks_yaml() == {}

    def test_malformed_yaml(self, fs):
        parser = make_base(fs, 'main', tasks_yaml='Task: [\n')
        with pytest.raises(DefinitionError) as e:
            parser.read_tasks_yaml()
        assert e.value.msg.startswith('Invalid yaml: ')
        assert e.value.file == parser.tasks_file

    def test_yaml_root_is_list(self, fs):
        parser = make_base(fs, 'main', tasks_yaml='- Task\n')
        with pytest.raises(DefinitionError) as e:
            parser.read_tasks_yaml()
        assert e.value.msg == 'Expected a mapping at yaml root, got list'
        assert e.value.file == parser.tasks_file


class TestReadI18nJson:
    def test_missing_file(self, fs):
        parser = make_base(fs, 'main')
        assert parser.read_i18n_json() == {}

    def test_valid_json(self, fs):
        parser = make_base(fs, 'main', i18n_json='{"Group": {"A": {"en-US": "A"}}}')
        assert parser.read_i18n_json() == {'Group': {'A': {'en-US': 'A'}}}

    def test_empty_file(self, fs):
        parser = make_base(fs, 'main', i18n_json='')
        assert parser.read_i18n_json() == {}

    def test_invalid_json(self, fs):
        parser = make_base(fs, 'main', i18n_json='{not json')
        with pytest.raises(DefinitionError) as e:
            parser.read_i18n_json()
        assert e.value.msg.startswith('Invalid json: ')
        assert e.value.file == parser.i18n_file

    def test_json_root_is_list(self, fs):
        parser = make_base(fs, 'main', i18n_json='[1, 2]')
        with pytest.raises(DefinitionError) as e:
            parser.read_i18n_json()
        assert e.value.msg == 'Expected a mapping at json root, got list'
        assert e.value.file == parser.i18n_file

    def test_postprocess_applied(self, fs, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @i18n_json_postprocess('main')
        def add_translation(data):
            data['Group'] = {'A': {'en-US': 'A'}}
            return data

        parser = make_base(fs, 'main', i18n_json='{}')
        assert parser.read_i18n_json() == {'Group': {'A': {'en-US': 'A'}}}

    def test_postprocess_chain_order(self, fs, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @i18n_json_postprocess('main')
        def first(data):
            data['A'] = 1
            return data

        @i18n_json_postprocess('main')
        def second(data):
            data['B'] = data['A'] + 1
            return data

        parser = make_base(fs, 'main', i18n_json='{}')
        assert parser.read_i18n_json() == {'A': 1, 'B': 2}

    def test_postprocess_returning_none_raises(self, fs, monkeypatch):
        """A postprocess returning None is a config error."""
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        calls = []

        @i18n_json_postprocess('main')
        def process(data):
            calls.append('process')
            return None

        parser = make_base(fs, 'main', i18n_json='{}')
        with pytest.raises(DefinitionError) as e:
            parser.read_i18n_json()
        assert e.value.msg == (
            'Postprocess function "process" returned None, '
            'did you forget "return data"?'
        )
        assert e.value.file == parser.i18n_file
        assert calls == ['process']

    def test_postprocess_chain_stops_after_none(self, fs, monkeypatch):
        """A None result stops the chain, the next postprocess does not run."""
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        calls = []

        @i18n_json_postprocess('main')
        def first(data):
            calls.append('first')
            return None

        @i18n_json_postprocess('main')
        def second(data):
            calls.append('second')
            return data

        parser = make_base(fs, 'main', i18n_json='{}')
        with pytest.raises(DefinitionError) as e:
            parser.read_i18n_json()
        assert e.value.msg == (
            'Postprocess function "first" returned None, '
            'did you forget "return data"?'
        )
        assert e.value.file == parser.i18n_file
        assert calls == ['first']

    def test_protected_nav_skips_postprocess(self, fs, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        calls = []

        @i18n_json_postprocess('alasio')
        def process(data):
            calls.append('process')
            return {'injected': True}

        parser = make_base(fs, 'alasio', i18n_json='{"Group": {}}')
        assert parser.read_i18n_json() == {'Group': {}}
        assert calls == []

    def test_no_postprocess_keeps_data(self, fs, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        parser = make_base(fs, 'main', i18n_json='{"Group": {}}')
        assert parser.read_i18n_json() == {'Group': {}}

    def test_postprocess_error_gets_file(self, fs, monkeypatch):
        """A postprocess class resolution error points at the i18n json."""
        info = PostprocessInfo.from_function(process_missing_class)
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {
            'main': [info],
        })
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        parser = make_base(fs, 'main', i18n_json='{}')
        with pytest.raises(DefinitionError) as e:
            parser.read_i18n_json()
        assert e.value.msg == (
            'Cannot resolve the class of postprocess function '
            '"process_missing_class" from __qualname__ '
            '"MissingClass.process", define it in a class body '
            'or use a 1-arg (data) function'
        )
        assert e.value.file == parser.i18n_file
