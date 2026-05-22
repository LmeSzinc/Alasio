from alasio.codegen.python.gen import CodeGenerator


class TestLiteralBasic:
    def test_literal_with_items(self):
        gen = CodeGenerator()
        with gen.Literal('fruit'):
            gen.Item('apple')
            gen.Item('banana')
        code = gen.write()
        expected = "fruit: Literal['apple', 'banana']\n"
        assert code == expected

    def test_literal_single_item(self):
        gen = CodeGenerator()
        with gen.Literal('mode'):
            gen.Item('auto')
        code = gen.write()
        expected = "mode: Literal['auto']\n"
        assert code == expected

    def test_literal_var_default(self):
        gen = CodeGenerator()
        with gen.Literal('fruit').Var('banana'):
            gen.Item('apple')
            gen.Item('banana')
        code = gen.write()
        expected = "fruit: Literal['apple', 'banana'] = 'banana'\n"
        assert code == expected

    def test_literal_set_literal_module(self):
        gen = CodeGenerator()
        with gen.Literal('color').set_literal('t.Literal'):
            gen.Item('red')
            gen.Item('green')
        code = gen.write()
        expected = "color: t.Literal['red', 'green']\n"
        assert code == expected

    def test_literal_set_literal_typing(self):
        gen = CodeGenerator()
        gen.Literal('status').set_literal('typing.Literal')
        code = gen.write()
        expected = "status: typing.Literal[]\n"
        assert code == expected

    def test_literal_no_items(self):
        gen = CodeGenerator()
        gen.Literal('flag')
        code = gen.write()
        expected = "flag: Literal[]\n"
        assert code == expected

    def test_literal_integer_items(self):
        gen = CodeGenerator()
        with gen.Literal('code'):
            gen.Item(200)
            gen.Item(404)
        code = gen.write()
        expected = "code: Literal[200, 404]\n"
        assert code == expected

    def test_literal_inside_class(self):
        gen = CodeGenerator()
        with gen.Class('Config'):
            with gen.Literal('env'):
                gen.Item('dev')
                gen.Item('prod')
        code = gen.write()
        expected = """\
class Config:
    env: Literal['dev', 'prod']
"""
        assert code == expected

    def test_literal_inside_def(self):
        gen = CodeGenerator()
        with gen.Def('setup'):
            gen.Literal('mode').Var('fast')
        code = gen.write()
        expected = """\
def setup():
    mode: Literal[] = 'fast'
"""
        assert code == expected


class TestLiteralChaining:
    def test_chaining_set_literal_and_var(self):
        gen = CodeGenerator()
        gen.Literal('size').set_literal('t.Literal').Var('medium')
        code = gen.write()
        expected = "size: t.Literal[] = 'medium'\n"
        assert code == expected

    def test_var_overrides(self):
        gen = CodeGenerator()
        with gen.Literal('direction'):
            gen.Item('north')
            gen.Item('south')
        code1 = gen.write()
        assert "= 'north'" not in code1

        gen2 = CodeGenerator()
        with gen2.Literal('direction').Var('south'):
            gen2.Item('north')
            gen2.Item('south')
        code2 = gen2.write()
        assert "= 'south'" in code2
        assert code1 != code2


class TestLiteralWrap:
    def test_wrap_always(self):
        gen = CodeGenerator()
        with gen.Literal('color').wrap('always'):
            gen.Item('red')
            gen.Item('green')
            gen.Item('blue')
        code = gen.write()
        expected = """\
color: Literal[
    'red',
    'green',
    'blue',
]
"""
        assert code == expected

    def test_wrap_always_with_default(self):
        gen = CodeGenerator()
        with gen.Literal('color').Var('red').wrap('always'):
            gen.Item('red')
            gen.Item('green')
        code = gen.write()
        expected = """\
color: Literal[
    'red',
    'green',
] = 'red'
"""
        assert code == expected

    def test_wrap_int_single_line(self):
        gen = CodeGenerator()
        with gen.Literal('fruit').wrap(80):
            gen.Item('apple')
            gen.Item('banana')
        code = gen.write()
        expected = "fruit: Literal['apple', 'banana']\n"
        assert code == expected

    def test_wrap_int_multiline(self):
        gen = CodeGenerator()
        with gen.Literal('fruit').wrap(20):
            gen.Item('short')
            gen.Item('medium')
            gen.Item('another')
        code = gen.write()
        expected = """\
fruit: Literal[
    'short',
    'medium',
    'another',
]
"""
        assert code == expected

    def test_wrap_false_inline(self):
        gen = CodeGenerator()
        with gen.Literal('mode').wrap(False):
            gen.Item('auto')
            gen.Item('manual')
        code = gen.write()
        expected = "mode: Literal['auto', 'manual']\n"
        assert code == expected

    def test_wrap_always_in_class(self):
        gen = CodeGenerator()
        with gen.Class('Config'):
            with gen.Literal('env').wrap('always'):
                gen.Item('dev')
                gen.Item('prod')
                gen.Item('staging')
        code = gen.write()
        expected = """\
class Config:
    env: Literal[
        'dev',
        'prod',
        'staging',
    ]
"""
        assert code == expected
