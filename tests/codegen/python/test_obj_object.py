from alasio.codegen.python.gen import CodeGen


class TestObjObject:
    def test_basic_with_name(self):
        gen = CodeGen()
        with gen.Object('btn', 'Button'):
            gen.Item('Click me')
            gen.Var('timeout', 10)
        code = gen.generate_str()
        expected = """\
btn = Button(
    Click me,
    timeout=10,
)
"""
        assert code == expected

    def test_with_anno(self):
        gen = CodeGen()
        with gen.Object('btn', 'Button').Anno('BtnType'):
            gen.Item('Click me')
        code = gen.generate_str()
        expected = """\
btn: BtnType = Button(
    Click me,
)
"""
        assert code == expected

    def test_without_name(self):
        gen = CodeGen()
        with gen.Object('', 'Button'):
            gen.Item('submit')
            gen.Var('delay', 5)
        code = gen.generate_str()
        expected = """\
Button(
    submit,
    delay=5,
)
"""
        assert code == expected

    def test_inline_wrap_false(self):
        """All args fit on one line with wrap(False)."""
        gen = CodeGen()
        with gen.Object('obj', 'MyClass').wrap(False):
            gen.Item('data')
            gen.Var('count', 42)
        code = gen.generate_str()
        assert code == "obj = MyClass(data, count=42)\n"

    def test_expand_wrap(self):
        gen = CodeGen()
        with gen.Object('dialog', 'Dialog').wrap('expand'):
            gen.Item('Confirm')
            gen.Var('width', 400)
            gen.Var('height', 300)
        code = gen.generate_str()
        expected = """\
dialog = Dialog(
    Confirm, width=400, height=300,
)
"""
        assert code == expected

    def test_empty_object(self):
        gen = CodeGen()
        with gen.Object('empty', 'EmptyClass'):
            pass  # no items
        code = gen.generate_str()
        assert code == "empty = EmptyClass()\n"

    def test_nested_in_list(self):
        """Object inside a List context."""
        gen = CodeGen()
        with gen.List('buttons'):
            with gen.Object('', 'Button').wrap(False):
                gen.Item('ok')
        code = gen.generate_str()
        expected = """\
buttons = [
    Button(ok),
]
"""
        assert code == expected

    def test_empty_anno_and_name(self):
        """Both name and anno empty - just the class name."""
        gen = CodeGen()
        with gen.Object('', 'func'):
            gen.Item('x')
            gen.Var('y', 1)
        code = gen.generate_str()
        expected = """\
func(
    x,
    y=1,
)
"""
        assert code == expected
