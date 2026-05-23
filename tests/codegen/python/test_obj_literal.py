from alasio.codegen.python.gen import CodeGen


class TestLiteralBasic:
    def test_literal_with_items(self):
        gen = CodeGen()
        with gen.Literal('fruit'):
            gen.Item('apple')
            gen.Item('banana')
        code = gen.generate_str()
        expected = "fruit: Literal['apple', 'banana']\n"
        assert code == expected

    def test_literal_single_item(self):
        gen = CodeGen()
        with gen.Literal('mode'):
            gen.Item('auto')
        code = gen.generate_str()
        expected = "mode: Literal['auto']\n"
        assert code == expected

    def test_literal_var_default(self):
        gen = CodeGen()
        with gen.Literal('fruit').Var('banana'):
            gen.Item('apple')
            gen.Item('banana')
        code = gen.generate_str()
        expected = "fruit: Literal['apple', 'banana'] = 'banana'\n"
        assert code == expected

    def test_literal_set_literal_module(self):
        gen = CodeGen()
        with gen.Literal('color').set_literal('t.Literal'):
            gen.Item('red')
            gen.Item('green')
        code = gen.generate_str()
        expected = "color: t.Literal['red', 'green']\n"
        assert code == expected

    def test_literal_set_literal_typing(self):
        gen = CodeGen()
        gen.Literal('status').set_literal('typing.Literal')
        code = gen.generate_str()
        expected = "status: typing.Literal[]\n"
        assert code == expected

    def test_literal_no_items(self):
        gen = CodeGen()
        gen.Literal('flag')
        code = gen.generate_str()
        expected = "flag: Literal[]\n"
        assert code == expected

    def test_literal_integer_items(self):
        gen = CodeGen()
        with gen.Literal('code'):
            gen.Item(200)
            gen.Item(404)
        code = gen.generate_str()
        expected = "code: Literal[200, 404]\n"
        assert code == expected

    def test_literal_inside_class(self):
        gen = CodeGen()
        with gen.Class('Config'):
            with gen.Literal('env'):
                gen.Item('dev')
                gen.Item('prod')
        code = gen.generate_str()
        expected = """\
class Config:
    env: Literal['dev', 'prod']
"""
        assert code == expected

    def test_literal_inside_def(self):
        gen = CodeGen()
        with gen.Def('setup'):
            gen.Literal('mode').Var('fast')
        code = gen.generate_str()
        expected = """\
def setup():
    mode: Literal[] = 'fast'
"""
        assert code == expected


class TestLiteralChaining:
    def test_chaining_set_literal_and_var(self):
        gen = CodeGen()
        gen.Literal('size').set_literal('t.Literal').Var('medium')
        code = gen.generate_str()
        expected = "size: t.Literal[] = 'medium'\n"
        assert code == expected

    def test_var_overrides(self):
        gen = CodeGen()
        with gen.Literal('direction'):
            gen.Item('north')
            gen.Item('south')
        code1 = gen.generate_str()
        assert "= 'north'" not in code1

        gen2 = CodeGen()
        with gen2.Literal('direction').Var('south'):
            gen2.Item('north')
            gen2.Item('south')
        code2 = gen2.generate_str()
        assert "= 'south'" in code2
        assert code1 != code2


class TestLiteralWrap:
    def test_wrap_always(self):
        gen = CodeGen()
        with gen.Literal('color').wrap('newline'):
            gen.Item('red')
            gen.Item('green')
            gen.Item('blue')
        code = gen.generate_str()
        expected = """\
color: Literal[
    'red',
    'green',
    'blue',
]
"""
        assert code == expected

    def test_wrap_always_with_default(self):
        gen = CodeGen()
        with gen.Literal('color').Var('red').wrap('newline'):
            gen.Item('red')
            gen.Item('green')
        code = gen.generate_str()
        expected = """\
color: Literal[
    'red',
    'green',
] = 'red'
"""
        assert code == expected

    def test_wrap_int_single_line(self):
        gen = CodeGen()
        with gen.Literal('fruit').wrap(80):
            gen.Item('apple')
            gen.Item('banana')
        code = gen.generate_str()
        expected = "fruit: Literal['apple', 'banana']\n"
        assert code == expected

    def test_wrap_int_multiline(self):
        gen = CodeGen()
        with gen.Literal('fruit').wrap(20):
            gen.Item('short')
            gen.Item('medium')
            gen.Item('another')
        code = gen.generate_str()
        expected = """\
fruit: Literal[
    'short',
    'medium',
    'another',
]
"""
        assert code == expected

    def test_wrap_false_inline(self):
        gen = CodeGen()
        with gen.Literal('mode').wrap('inline'):
            gen.Item('auto')
            gen.Item('manual')
        code = gen.generate_str()
        expected = "mode: Literal['auto', 'manual']\n"
        assert code == expected

    def test_wrap_always_in_class(self):
        gen = CodeGen()
        with gen.Class('Config'):
            with gen.Literal('env').wrap('newline'):
                gen.Item('dev')
                gen.Item('prod')
                gen.Item('staging')
        code = gen.generate_str()
        expected = """\
class Config:
    env: Literal[
        'dev',
        'prod',
        'staging',
    ]
"""
        assert code == expected

    def test_wrap_expand_single_row(self):
        """All items fit on one line inside expanded brackets."""
        gen = CodeGen()
        with gen.Literal('mode').wrap('expand'):
            gen.Item('a')
            gen.Item('b')
        code = gen.generate_str()
        expected = "mode: Literal[\n    'a', 'b',\n]\n"
        assert code == expected

    def test_wrap_expand_with_default(self):
        gen = CodeGen()
        with gen.Literal('color').Var('red').wrap('expand'):
            gen.Item('red')
            gen.Item('green')
        code = gen.generate_str()
        expected = "color: Literal[\n    'red', 'green',\n] = 'red'\n"
        assert code == expected
