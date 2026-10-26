"""
Tests of PostprocessInfo and run_postprocess().

A postprocess function is validated when the decorator registers it, its call
plan is cached in PostprocessInfo, and run_postprocess() only uses the cached
plan. The registry is class level, so every test replaces it with a fresh dict
through monkeypatch.
"""

import pytest

from alasio.config_dev.parse.base import (
    ALASIO_PACKAGE, DefinitionError, ParseBase, args_yaml_postprocess, defined_by_alasio, i18n_json_postprocess
)
from alasio.config_dev.parse.postprocess import PostprocessInfo, run_postprocess
from alasio.logger import logger

DATA = {'Group': {'args': {'A': 1}}}


def process_one_arg(data):
    return ('one', data)


def process_two_args(cls, data):
    return (cls, data)


def process_data_keyword_only(*, data):
    return ('data-kw', data)


def process_config_keyword_only(*, config):
    return ('config-kw', config)


def process_class_config_keyword_only(cls, *, config):
    return (cls, config)


def process_data_and_flag(data, *, flag):
    return (data, flag)


def process_data_with_default(data, optional=None):
    return (data, optional)


class ClassProcessor:
    """A module level class, from_function resolves it from __qualname__."""

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


class KeywordOnlyClassProcessor:
    def process(cls, *, data):
        return ('class-kw', cls, data)


class KeywordOnlyInstanceProcessor:
    def process(self, *, data):
        return ('instance-kw', self, data)


class KeywordOnlyCallableProcessor:
    def __call__(self, *, data):
        return ('callable-kw', data)


class ClassCallableProcessor:
    def __call__(self, cls, data):
        return (cls, data)


class ClassArgInstanceProcessor:
    def process(self, cls, data):
        return (cls, data)


class OptionalArgInstanceProcessor:
    def process(self, data, optional=None):
        return ('optional', data, optional)


def make_nested_processor():
    def process(cls, data):
        return (cls, data)

    return process


def make_dynamic_processor():
    class Dynamic:
        def process(cls, data):
            return (cls, data)

    return Dynamic.process


def make_info(func):
    return PostprocessInfo.from_function(func)


def run(func, data=DATA):
    return run_postprocess(make_info(func), data)


def assert_prewarmed(info):
    assert set(info.__dict__) == {'name', 'signature', 'plan'}


def assert_registered_list(infos, expected):
    assert [info.func for info in infos] == [func for func, _ in expected]
    assert [info.plan for info in infos] == [plan for _, plan in expected]
    for info in infos:
        assert_prewarmed(info)


def assert_registered(infos, func, plan):
    assert_registered_list(infos, [(func, plan)])


def pretend_in_alasio(func):
    """
    Pretend a function of the tests is written in the alasio package.

    Args:
        func (callable): Function to move

    Returns:
        callable: The same function
    """
    file = f'{ALASIO_PACKAGE}/config_dev/gen_alasio.py'
    func.__code__ = func.__code__.replace(co_filename=file)
    return func


class TestDefinedByAlasio:
    """
    Only alasio itself may postprocess the navs of the alasio entry.

    The ownership is the file the postprocess is written in, the tests are not
    a part of the alasio package.
    """

    def test_test_code(self):
        def process(data):
            return data

        assert defined_by_alasio(process) is False

    def test_alasio_code(self):
        def process(data):
            return data

        assert defined_by_alasio(pretend_in_alasio(process)) is True

    def test_alas_i18n_json_of_the_alasio_generator(self):
        from alasio.config_dev.gen_alasio import Generator
        assert defined_by_alasio(Generator.alas_i18n_json) is True


class TestPostprocessInfoFromFunction:
    def test_from_function_preheats_cached_values(self):
        info = make_info(process_one_arg)
        assert info.__dict__ == {
            'name': 'process_one_arg',
            'signature': '(data)',
            'plan': ('data', ''),
        }

    def test_one_arg_function(self):
        info = make_info(process_one_arg)
        assert info.func is process_one_arg
        assert info.plan == ('data', '')

    def test_class_level_function(self):
        info = make_info(ClassProcessor.process)
        assert info.func is ClassProcessor.process
        assert info.plan == ('cls_data', '')

    def test_keyword_only_data(self):
        info = make_info(process_data_keyword_only)
        assert info.plan == ('data_kw', 'data')

    def test_single_keyword_only_argument_is_the_data(self):
        info = make_info(process_config_keyword_only)
        assert info.plan == ('data_kw', 'config')

    def test_keyword_only_class_data(self):
        info = make_info(KeywordOnlyClassProcessor.process)
        assert info.plan == ('cls_data_kw', 'data')

    def test_two_arg_function_without_class(self):
        """A module level two-arg function can never resolve a class."""
        with pytest.raises(DefinitionError) as e:
            make_info(process_two_args)
        assert e.value.msg == (
            'Cannot resolve the class of postprocess function "process_two_args" '
            'from __qualname__ "process_two_args", define it in a class body '
            'or use a 1-arg (data) function'
        )

    def test_nested_function_has_no_class(self):
        with pytest.raises(DefinitionError) as e:
            make_info(make_nested_processor())
        assert e.value.msg == (
            'Cannot resolve the class of postprocess function "process" '
            'from __qualname__ "make_nested_processor.<locals>.process", '
            'define it in a class body or use a 1-arg (data) function'
        )

    def test_dynamic_class_has_no_module_class(self):
        with pytest.raises(DefinitionError) as e:
            make_info(make_dynamic_processor())
        assert e.value.msg.startswith(
            'Cannot resolve the class of postprocess function "process" '
            'from __qualname__ "make_dynamic_processor.<locals>.Dynamic.process"')

    def test_broken_qualname_is_accepted_until_runtime(self):
        """A qualname with a dot may still fail to resolve at call time."""
        def process(cls, data):
            return (cls, data)

        process.__qualname__ = 'MissingClass.process'
        info = make_info(process)
        assert info.plan == ('cls_data', '')
        with pytest.raises(DefinitionError) as e:
            run_postprocess(info, DATA)
        assert e.value.msg == (
            'Cannot resolve the class of postprocess function "process" '
            'from __qualname__ "MissingClass.process", define it in a class body '
            'or use a 1-arg (data) function'
        )

    def test_callable_object_with_class_argument(self):
        with pytest.raises(DefinitionError) as e:
            make_info(ClassCallableProcessor())
        assert e.value.msg == (
            'Postprocess function "ClassCallableProcessor" is not a plain function, '
            'it can not receive a resolved class, use a 1-arg (data) function'
        )

    def test_bound_method_with_class_argument(self):
        with pytest.raises(DefinitionError) as e:
            make_info(ClassArgInstanceProcessor().process)
        assert e.value.msg == (
            'Postprocess function "process" is not a plain function, '
            'it can not receive a resolved class, use a 1-arg (data) function'
        )

    @pytest.mark.parametrize('func, name, signature', [
        (lambda: None, '<lambda>', '()'),
        (lambda a, b, c: None, '<lambda>', '(a, b, c)'),
        (process_data_and_flag, 'process_data_and_flag', '(data, *, flag)'),
        (process_class_config_keyword_only, 'process_class_config_keyword_only', '(cls, *, config)'),
    ], ids=['zero', 'three', 'data-and-flag', 'class-config'])
    def test_invalid_signature(self, func, name, signature):
        with pytest.raises(DefinitionError) as e:
            make_info(func)
        assert e.value.msg == (
            f'Invalid postprocess signature for "{name}", '
            f'must accept (data) or (cls, data), got {signature}'
        )


class TestRunPostprocess:
    def test_one_arg_function(self):
        assert run(process_one_arg) == ('one', DATA)

    def test_class_level_function(self):
        assert run(ClassProcessor.process) == ('class', ClassProcessor, DATA)

    def test_bound_classmethod(self):
        assert run(ClassMethodProcessor.process) == ('classmethod', ClassMethodProcessor, DATA)

    def test_bound_instance_method(self):
        processor = InstanceProcessor()
        assert run(processor.process) == ('instance', processor, DATA)

    def test_callable_object(self):
        assert run(CallableProcessor()) == ('callable', DATA)

    def test_keyword_only_data(self):
        assert run(process_data_keyword_only) == ('data-kw', DATA)

    def test_single_keyword_only_argument_is_the_data(self):
        assert run(process_config_keyword_only) == ('config-kw', DATA)

    def test_one_arg_with_optional_extra(self):
        assert run(process_data_with_default) == (DATA, None)

    def test_keyword_only_class_data(self):
        assert run(KeywordOnlyClassProcessor.process) == (
            'class-kw', KeywordOnlyClassProcessor, DATA,
        )

    def test_bound_method_keyword_only_data(self):
        processor = KeywordOnlyInstanceProcessor()
        assert run(processor.process) == ('instance-kw', processor, DATA)

    def test_keyword_only_callable(self):
        assert run(KeywordOnlyCallableProcessor()) == ('callable-kw', DATA)

    def test_bound_method_with_optional_extra(self):
        processor = OptionalArgInstanceProcessor()
        assert run(processor.process) == ('optional', DATA, None)

    def test_empty_plan(self):
        info = PostprocessInfo(func=process_one_arg)
        info.__dict__['plan'] = ()
        with pytest.raises(DefinitionError) as e:
            run_postprocess(info, DATA)
        assert e.value.msg == (
            'Postprocess function "process_one_arg" has no call plan, '
            'build it with PostprocessInfo.from_function()'
        )

    def test_direct_construction_computes_plan_lazily(self):
        info = PostprocessInfo(func=process_one_arg)
        assert 'plan' not in info.__dict__
        assert run_postprocess(info, DATA) == ('one', DATA)
        assert info.__dict__['plan'] == ('data', '')

    def test_none_result(self):
        """A forgotten return is a config error, None is never a valid result."""
        def process(data):
            data['A'] = 1

        info = make_info(process)
        with pytest.raises(DefinitionError) as e:
            run_postprocess(info, DATA)
        assert e.value.msg == (
            'Postprocess function "process" returned None, '
            'did you forget "return data"?'
        )

    def test_invalid_plan(self):
        info = PostprocessInfo(func=process_one_arg)
        info.__dict__['plan'] = ('bogus', '')
        with pytest.raises(DefinitionError) as e:
            run_postprocess(info, DATA)
        assert e.value.msg == (
            "Invalid postprocess plan for \"process_one_arg\": ('bogus', '')"
        )

    def test_bound_method_with_class_argument_at_runtime(self):
        """The registration rejects this, direct construction must fail too."""
        processor = ClassArgInstanceProcessor()
        info = PostprocessInfo(func=processor.process)
        info.__dict__['plan'] = ('cls_data', '')
        with pytest.raises(DefinitionError) as e:
            run_postprocess(info, DATA)
        assert e.value.msg == (
            'Postprocess function "process" is already bound, '
            'it must accept (data), got (cls, data)'
        )


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
        assert_registered(ParseBase.dict_args_yaml_postprocess['main'], process, ('data', ''))

    def test_registers_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        args_yaml_postprocess('main')(ClassProcessor.process)
        assert_registered(
            ParseBase.dict_args_yaml_postprocess['main'], ClassProcessor.process, ('cls_data', ''))

    def test_registers_keyword_only_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        args_yaml_postprocess('main')(KeywordOnlyClassProcessor.process)
        assert_registered(
            ParseBase.dict_args_yaml_postprocess['main'],
            KeywordOnlyClassProcessor.process, ('cls_data_kw', 'data'))

    def test_rejects_callable_object_with_class_argument(self, monkeypatch):
        """A callable object can not provide the owning class."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        processor = ClassCallableProcessor()

        with pytest.raises(DefinitionError) as e:
            args_yaml_postprocess('main')(processor)
        assert e.value.msg == (
            'Postprocess function "ClassCallableProcessor" is not a plain function, '
            'it can not receive a resolved class, use a 1-arg (data) function'
        )
        assert ParseBase.dict_args_yaml_postprocess == {}

    def test_registers_callable_object_as_one_arg(self, monkeypatch):
        """An object without __code__ falls back to the 1 argument form."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        processor = CallableProcessor()
        assert args_yaml_postprocess('main')(processor) is processor
        assert_registered(ParseBase.dict_args_yaml_postprocess['main'], processor, ('data', ''))

    def test_rejects_module_level_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        with pytest.raises(DefinitionError) as e:
            args_yaml_postprocess('main')(process_two_args)
        assert e.value.msg == (
            'Cannot resolve the class of postprocess function "process_two_args" '
            'from __qualname__ "process_two_args", define it in a class body '
            'or use a 1-arg (data) function'
        )
        assert ParseBase.dict_args_yaml_postprocess == {}

    def test_rejects_nested_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        with pytest.raises(DefinitionError) as e:
            args_yaml_postprocess('main')(make_nested_processor())
        assert e.value.msg == (
            'Cannot resolve the class of postprocess function "process" '
            'from __qualname__ "make_nested_processor.<locals>.process", '
            'define it in a class body or use a 1-arg (data) function'
        )
        assert ParseBase.dict_args_yaml_postprocess == {}

    def test_keeps_registration_order(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @args_yaml_postprocess('main')
        def first(data):
            return data

        @args_yaml_postprocess('main')
        def second(data):
            return data

        assert ParseBase.dict_args_yaml_postprocess['main'] == [
            PostprocessInfo(func=first),
            PostprocessInfo(func=second),
        ]
        assert_registered_list(
            ParseBase.dict_args_yaml_postprocess['main'],
            [(first, ('data', '')), (second, ('data', ''))])

    def test_navs_are_independent(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @args_yaml_postprocess('main')
        def process_main(data):
            return data

        @args_yaml_postprocess('gems')
        def process_gems(data):
            return data

        assert set(ParseBase.dict_args_yaml_postprocess) == {'main', 'gems'}
        assert_registered(
            ParseBase.dict_args_yaml_postprocess['main'], process_main, ('data', ''))
        assert_registered(
            ParseBase.dict_args_yaml_postprocess['gems'], process_gems, ('data', ''))

    @pytest.mark.parametrize('argcount, signature', [
        (0, '()'),
        (3, '(a, b, c)'),
    ], ids=['zero', 'three'])
    def test_rejects_wrong_argument_count(self, monkeypatch, argcount, signature):
        """Only the supported (data) / (cls, data) forms are registered."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        if argcount == 0:
            def process():
                return DATA
        else:
            def process(a, b, c):
                return DATA

        with pytest.raises(DefinitionError) as e:
            args_yaml_postprocess('main')(process)
        assert e.value.msg == (
            f'Invalid postprocess signature for "process", '
            f'must accept (data) or (cls, data), got {signature}'
        )
        assert 'main' not in ParseBase.dict_args_yaml_postprocess

    @pytest.mark.parametrize('nav', ['alasio', 'device'])
    def test_protected_nav(self, monkeypatch, nav):
        """A protected nav name never accepts a postprocess."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(data):
            return data

        with logger.mock_capture_writer() as capture:
            assert args_yaml_postprocess(nav)(process) is process
        assert ParseBase.dict_args_yaml_postprocess == {}
        assert capture.concat() == (
            f'WARNING | {nav} is a protected nav name, postprocess ignored\n'
        )

    def test_alasio_itself_may_register(self, monkeypatch):
        """The navs of the alasio entry are postprocessed by alasio itself."""
        monkeypatch.setattr(ParseBase, 'dict_args_yaml_postprocess', {})

        def process(data):
            return data

        process = pretend_in_alasio(process)
        assert args_yaml_postprocess('alasio')(process) is process
        assert_registered(
            ParseBase.dict_args_yaml_postprocess['alasio'], process, ('data', ''))


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
        assert_registered(ParseBase.dict_i18n_json_postprocess['main'], process, ('data', ''))

    def test_registers_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        i18n_json_postprocess('main')(ClassProcessor.process)
        assert_registered(
            ParseBase.dict_i18n_json_postprocess['main'], ClassProcessor.process, ('cls_data', ''))

    def test_registers_keyword_only_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        i18n_json_postprocess('main')(KeywordOnlyClassProcessor.process)
        assert_registered(
            ParseBase.dict_i18n_json_postprocess['main'],
            KeywordOnlyClassProcessor.process, ('cls_data_kw', 'data'))

    def test_rejects_callable_object_with_class_argument(self, monkeypatch):
        """A callable object can not provide the owning class."""
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        processor = ClassCallableProcessor()

        with pytest.raises(DefinitionError) as e:
            i18n_json_postprocess('main')(processor)
        assert e.value.msg == (
            'Postprocess function "ClassCallableProcessor" is not a plain function, '
            'it can not receive a resolved class, use a 1-arg (data) function'
        )
        assert ParseBase.dict_i18n_json_postprocess == {}

    def test_registers_callable_object_as_one_arg(self, monkeypatch):
        """An object without __code__ falls back to the 1 argument form."""
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})
        processor = CallableProcessor()
        assert i18n_json_postprocess('main')(processor) is processor
        assert_registered(ParseBase.dict_i18n_json_postprocess['main'], processor, ('data', ''))

    def test_rejects_module_level_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        with pytest.raises(DefinitionError) as e:
            i18n_json_postprocess('main')(process_two_args)
        assert e.value.msg == (
            'Cannot resolve the class of postprocess function "process_two_args" '
            'from __qualname__ "process_two_args", define it in a class body '
            'or use a 1-arg (data) function'
        )
        assert ParseBase.dict_i18n_json_postprocess == {}

    def test_rejects_nested_two_arg_function(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        with pytest.raises(DefinitionError) as e:
            i18n_json_postprocess('main')(make_nested_processor())
        assert e.value.msg == (
            'Cannot resolve the class of postprocess function "process" '
            'from __qualname__ "make_nested_processor.<locals>.process", '
            'define it in a class body or use a 1-arg (data) function'
        )
        assert ParseBase.dict_i18n_json_postprocess == {}

    def test_keeps_registration_order(self, monkeypatch):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        @i18n_json_postprocess('main')
        def first(data):
            return data

        @i18n_json_postprocess('main')
        def second(data):
            return data

        assert ParseBase.dict_i18n_json_postprocess['main'] == [
            PostprocessInfo(func=first),
            PostprocessInfo(func=second),
        ]
        assert_registered_list(
            ParseBase.dict_i18n_json_postprocess['main'],
            [(first, ('data', '')), (second, ('data', ''))])

    @pytest.mark.parametrize('argcount, signature', [
        (0, '()'),
        (3, '(a, b, c)'),
    ], ids=['zero', 'three'])
    def test_rejects_wrong_argument_count(self, monkeypatch, argcount, signature):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        if argcount == 0:
            def process():
                return DATA
        else:
            def process(a, b, c):
                return DATA

        with pytest.raises(DefinitionError) as e:
            i18n_json_postprocess('main')(process)
        assert e.value.msg == (
            f'Invalid postprocess signature for "process", '
            f'must accept (data) or (cls, data), got {signature}'
        )
        assert 'main' not in ParseBase.dict_i18n_json_postprocess

    @pytest.mark.parametrize('nav', ['alasio', 'device'])
    def test_protected_nav(self, monkeypatch, nav):
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})
        monkeypatch.setattr(ParseBase, 'alasio_nav', {'alasio', 'device'})

        def process(data):
            return data

        with logger.mock_capture_writer() as capture:
            assert i18n_json_postprocess(nav)(process) is process
        assert ParseBase.dict_i18n_json_postprocess == {}
        assert capture.concat() == (
            f'WARNING | {nav} is a protected nav name, postprocess ignored\n'
        )

    def test_alasio_itself_may_register(self, monkeypatch):
        """The navs of the alasio entry are postprocessed by alasio itself."""
        monkeypatch.setattr(ParseBase, 'dict_i18n_json_postprocess', {})

        def process(data):
            return data

        process = pretend_in_alasio(process)
        assert i18n_json_postprocess('alasio')(process) is process
        assert_registered(
            ParseBase.dict_i18n_json_postprocess['alasio'], process, ('data', ''))
