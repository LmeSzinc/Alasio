"""Tests for If, Elif, Else code generation."""
from alasio.codegen.python.gen import CodeGen


class TestIf:
    """Basic if block generation."""

    def test_if_simple(self):
        gen = CodeGen()
        with gen.If('x > 0'):
            gen.Var('result', 'positive')
        code = gen.generate_str()
        expected = """\
if x > 0:
    result = 'positive'
"""
        assert code == expected

    def test_if_empty_emits_pass(self):
        gen = CodeGen()
        with gen.If('x > 0'):
            pass
        code = gen.generate_str()
        expected = """\
if x > 0:
    pass
"""
        assert code == expected

    def test_if_multiple_statements(self):
        gen = CodeGen()
        with gen.If('x > 0'):
            gen.Var('a', 1)
            gen.Var('b', 2)
        code = gen.generate_str()
        expected = """\
if x > 0:
    a = 1
    b = 2
"""
        assert code == expected


class TestIfElse:
    """If-else block generation."""

    def test_if_else(self):
        gen = CodeGen()
        with gen.If('x > 0'):
            gen.Var('result', 'positive')
        with gen.Else():
            gen.Var('result', 'non_positive')
        code = gen.generate_str()
        expected = """\
if x > 0:
    result = 'positive'
else:
    result = 'non_positive'
"""
        assert code == expected

    def test_empty_else_emits_pass(self):
        gen = CodeGen()
        with gen.If('x > 0'):
            gen.Var('result', 'positive')
        with gen.Else():
            pass
        code = gen.generate_str()
        expected = """\
if x > 0:
    result = 'positive'
else:
    pass
"""
        assert code == expected


class TestIfElifElse:
    """If-elif-else block generation."""

    def test_if_elif_else(self):
        gen = CodeGen()
        with gen.If('x > 0'):
            gen.Var('result', 'positive')
        with gen.Elif('x == 0'):
            gen.Var('result', 'zero')
        with gen.Else():
            gen.Var('result', 'negative')
        code = gen.generate_str()
        expected = """\
if x > 0:
    result = 'positive'
elif x == 0:
    result = 'zero'
else:
    result = 'negative'
"""
        assert code == expected

    def test_if_elif_elif_else(self):
        gen = CodeGen()
        with gen.If('x > 0'):
            gen.Var('r', 'positive')
        with gen.Elif('x == 0'):
            gen.Var('r', 'zero')
        with gen.Elif('x == -1'):
            gen.Var('r', 'minus_one')
        with gen.Else():
            gen.Var('r', 'other')
        code = gen.generate_str()
        expected = """\
if x > 0:
    r = 'positive'
elif x == 0:
    r = 'zero'
elif x == -1:
    r = 'minus_one'
else:
    r = 'other'
"""
        assert code == expected


class TestNestedIf:
    """Nested if blocks."""

    def test_nested_if_inside_if(self):
        gen = CodeGen()
        with gen.If('x > 0'):
            with gen.If('x > 10'):
                gen.Var('result', 'large')
            with gen.Else():
                gen.Var('result', 'small_positive')
        with gen.Else():
            gen.Var('result', 'non_positive')
        code = gen.generate_str()
        expected = """\
if x > 0:
    if x > 10:
        result = 'large'
    else:
        result = 'small_positive'
else:
    result = 'non_positive'
"""
        assert code == expected


class TestIfInsideFunction:
    """If blocks inside a function."""

    def test_if_inside_def(self):
        gen = CodeGen()
        with gen.Def('classify').set_args('x'):
            with gen.If('x > 0'):
                gen.Var('result', 'positive')
            with gen.Elif('x == 0'):
                gen.Var('result', 'zero')
            with gen.Else():
                gen.Var('result', 'negative')
            gen.Raw('return result')
        code = gen.generate_str()
        expected = """\
def classify(x):
    if x > 0:
        result = 'positive'
    elif x == 0:
        result = 'zero'
    else:
        result = 'negative'
    return result
"""
        assert code == expected
