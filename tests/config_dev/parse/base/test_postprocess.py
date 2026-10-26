"""
Tests of the postprocess registry and ParseBase._run_postprocess().

A postprocess function may be either a plain function receiving the data, or a
function receiving (cls, data) that is resolved from its __qualname__ (see the
class method example of args_yaml_postprocess). The registry is class level, so
every test replaces it with a fresh dict through monkeypatch.
"""

import pytest

from alasio.config_dev.parse.base import ParseBase, args_yaml_postprocess, i18n_json_postprocess

DATA = {'Group': {'args': {'A': 1}}}


def process_one_arg(data):
    return ('one', data)


def process_two_args(cls, data):
    return (cls, data)


class ClassProcessor:
    """A module level class, _run_postprocess resolves it from __qualname__."""

    def process(cls, data):
        return ('class', cls, data)


class ClassMethodProcessor:
    @classmethod
    def process(cls, data):
        return ('classmethod', cls, data)


class InstanceProcessor:
    def process(self, data):
        return ('instance', self, data)


class CallableProcessor:
    def __call__(self, data):
        return ('callable', data)


def make_nested_processor():
    def process(cls, data):
        return (cls, data)

    return process


class TestRunPostprocess:
    def test_one_arg_function(self):
        """A plain function receives the data."""
        assert ParseBase._run_postprocess(process_one_arg, DATA) == ('one', DATA)

    def test_two_arg_function_without_class(self):
        """A two-arg function without a resolvable class receives None."""
        assert ParseBase._run_postprocess(process_two_args, DATA) == (None, DATA)

    def test_class_level_function(self):
        """A module level class is resolved and passed as cls."""
        assert ParseBase._run_postprocess(ClassProcessor.process, DATA) == (
            'class', ClassProcessor, DATA,
        )

    def test_nested_function_has_no_class(self):
        """A function nested in another function can not resolve a class."""
        assert ParseBase._run_postprocess(make_nested_processor(), DATA) == (None, DATA)

    def test_bound_classmethod(self):
        """A bound classmethod already carries its class, it receives data only."""
        assert ParseBase._run_postprocess(ClassMethodProcessor.process, DATA) == (
            'classmethod', ClassMethodProcessor, DATA,
        )

    def test_bound_instance_method(self):
        """A bound instance method already carries its instance, it receives data only."""
        processor = InstanceProcessor()
        assert ParseBase._run_postprocess(processor.process, DATA) == ('instance', processor, DATA)

    def test_callable_object(self):
        """An object without __code__ is called with the data only."""
        assert ParseBase._run_postprocess(CallableProcessor(), DATA) == ('callable', DATA)


class TestArgsYamlPostprocessDecorator:
    def test_returns_the_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(data):
            return data

        assert args_yaml_postprocess('main')(process) is process

    def test_registers_one_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(data):
            return data

        args_yaml_postprocess('main')(process)
        assert ParseBase.dict_args_yaml_postprocess == {'main': [process]}

    def test_registers_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(cls, data):
            return data

        args_yaml_postprocess('main')(process)
        assert ParseBase.dict_args_yaml_postprocess == {'main': [process]}

    def test_registers_callable_object_as_one_arg(self, monkeypatch):
        """An object without __code__ falls back to the 1 argument form."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        processor = CallableProcessor()
        assert args_yaml_postprocess('main')(processor) is processor
        assert ParseBase.dict_args_yaml_postprocess == {'main': [processor]}

    def test_keeps_registration_order(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @args_yaml_postprocess('main')
        def first(data):
            return data

        @args_yaml_postprocess('main')
        def second(data):
            return data

        assert ParseBase.dict_args_yaml_postprocess['main'] == [first, second]

    def test_navs_are_independent(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @args_yaml_postprocess('main')
        def process_main(data):
            return data

        @args_yaml_postprocess('gems')
        def process_gems(data):
            return data

        assert ParseBase.dict_args_yaml_postprocess == {
            'main': [process_main],
            'gems': [process_gems],
        }

    @pytest.mark.parametrize('argcount', [0, 3], ids=['zero', 'three'])
    def test_rejects_wrong_argument_count(self, monkeypatch, capsys, argcount):
        """Only 1 or 2 positional arguments are accepted, the function is ignored."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        if argcount == 0:
            def process():
                return DATA
        else:
            def process(a, b, c):
                return DATA

        assert args_yaml_postprocess('main')(process) is process
        assert 'main' not in ParseBase.dict_args_yaml_postprocess
        assert capsys.readouterr().out == (
            f'Warning: Postprocess function "process" must have 1 or 2 arguments, '
            f'got {argcount}. Ignoring.\n'
        )

    @pytest.mark.parametrize('nav', ['alasio', 'device'])
    def test_protected_nav(self, monkeypatch, capsys, nav):
        """A protected nav name never accepts a postprocess."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(data):
            return data

        assert args_yaml_postprocess(nav)(process) is process
        assert ParseBase.dict_args_yaml_postprocess == {}
        assert capsys.readouterr().out == (
            f'Warning: {nav} is a protected nav name, postprocess ignored\n'
        )


class TestI18nJsonPostprocessDecorator:
    def test_returns_the_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(data):
            return data

        assert i18n_json_postprocess('main')(process) is process

    def test_registers_one_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(data):
            return data

        i18n_json_postprocess('main')(process)
        assert ParseBase.dict_i18n_json_postprocess == {'main': [process]}

    def test_registers_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(cls, data):
            return data

        i18n_json_postprocess('main')(process)
        assert ParseBase.dict_i18n_json_postprocess == {'main': [process]}

    def test_registers_callable_object_as_one_arg(self, monkeypatch):
        """An object without __code__ falls back to the 1 argument form."""
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        processor = CallableProcessor()
        assert i18n_json_postprocess('main')(processor) is processor
        assert ParseBase.dict_i18n_json_postprocess == {'main': [processor]}

    def test_keeps_registration_order(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @i18n_json_postprocess('main')
        def first(data):
            return data

        @i18n_json_postprocess('main')
        def second(data):
            return data

        assert ParseBase.dict_i18n_json_postprocess['main'] == [first, second]

    @pytest.mark.parametrize('argcount', [0, 3], ids=['zero', 'three'])
    def test_rejects_wrong_argument_count(self, monkeypatch, capsys, argcount):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        if argcount == 0:
            def process():
                return DATA
        else:
            def process(a, b, c):
                return DATA

        assert i18n_json_postprocess('main')(process) is process
        assert 'main' not in ParseBase.dict_i18n_json_postprocess
        assert capsys.readouterr().out == (
            f'Warning: Postprocess function "process" must have 1 or 2 arguments, '
            f'got {argcount}. Ignoring.\n'
        )

    @pytest.mark.parametrize('nav', ['alasio', 'device'])
    def test_protected_nav(self, monkeypatch, capsys, nav):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(data):
            return data

        assert i18n_json_postprocess(nav)(process) is process
        assert ParseBase.dict_i18n_json_postprocess == {}
        assert capsys.readouterr().out == (
            f'Warning: {nav} is a protected nav name, postprocess ignored\n'
        )
