from alasio.codegen.python.gen import CodeGen
from alasio.codegen.python.obj_base import ReprWrapper


class TestObjObject:
    def test_basic_with_name(self):
        gen = CodeGen()
        with gen.Object('btn', 'Button'):
            gen.Item('Click me')
            gen.Var('timeout', 10)
        code = gen.generate_str()
        expected = """\
btn = Button(
    'Click me',
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
    'Click me',
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
    'submit',
    delay=5,
)
"""
        assert code == expected

    def test_inline_wrap_false(self):
        """All args fit on one line with wrap('inline')."""
        gen = CodeGen()
        with gen.Object('obj', 'MyClass').wrap('inline'):
            gen.Item('data')
            gen.Var('count', 42)
        code = gen.generate_str()
        assert code == "obj = MyClass('data', count=42)\n"

    def test_expand_wrap(self):
        gen = CodeGen()
        with gen.Object('dialog', 'Dialog').wrap('expand'):
            gen.Item('Confirm')
            gen.Var('width', 400)
            gen.Var('height', 300)
        code = gen.generate_str()
        expected = """\
dialog = Dialog(
    'Confirm', width=400, height=300,
)
"""
        assert code == expected

    # ── Nested Object tests ──────────────────────────────────────────────

    def test_nested_object(self):
        """Object nested inside another Object."""
        gen = CodeGen()
        with gen.Object('outer', 'OuterClass'):
            gen.Item('simple_arg')
            gen.Var('simple_kw', 1)
            with gen.Object('inner', 'InnerClass'):
                gen.Item('inner_arg')
        code = gen.generate_str()
        expected = """\
outer = OuterClass(
    'simple_arg',
    simple_kw=1,
    inner=InnerClass(
        'inner_arg',
    ),
)
"""
        assert code == expected

    def test_nested_object_with_anno(self):
        """Nested object with type annotation on the child."""
        gen = CodeGen()
        with gen.Object('outer', 'OuterClass'):
            gen.Item('item1')
            with gen.Object('inner', 'InnerClass').Anno('InnerType'):
                gen.Item('inner_arg')
                gen.Var('x', 99)
        code = gen.generate_str()
        expected = """\
outer = OuterClass(
    'item1',
    inner: InnerType = InnerClass(
        'inner_arg',
        x=99,
    ),
)
"""
        assert code == expected

    def test_nested_object_without_name(self):
        """Nested object without a variable name (just the class)."""
        gen = CodeGen()
        with gen.Object('outer', 'OuterClass'):
            with gen.Object('', 'InnerClass'):
                gen.Item('inner_arg')
        code = gen.generate_str()
        expected = """\
outer = OuterClass(
    InnerClass(
        'inner_arg',
    ),
)
"""
        assert code == expected

    def test_nested_object_inline_wrap(self):
        """Nested object with inline wrapping."""
        gen = CodeGen()
        with gen.Object('outer', 'OuterClass'):
            with gen.Object('inner', 'InnerClass').wrap('inline'):
                gen.Item('data')
                gen.Var('count', 42)
        code = gen.generate_str()
        expected = """\
outer = OuterClass(
    inner=InnerClass('data', count=42),
)
"""
        assert code == expected

    def test_triple_nested_objects(self):
        """Three levels of nested Object."""
        gen = CodeGen()
        with gen.Object('level1', 'L1'):
            gen.Var('a', 1)
            with gen.Object('level2', 'L2'):
                gen.Var('b', 2)
                with gen.Object('level3', 'L3'):
                    gen.Var('c', 3)
        code = gen.generate_str()
        expected = """\
level1 = L1(
    a=1,
    level2=L2(
        b=2,
        level3=L3(
            c=3,
        ),
    ),
)
"""
        assert code == expected

    def test_sibling_nested_objects(self):
        """Multiple nested objects at the same level inside a parent."""
        gen = CodeGen()
        with gen.Object('outer', 'OuterClass'):
            gen.Var('name', 'test')
            with gen.Object('child_a', 'ChildA'):
                gen.Item('a')
            with gen.Object('child_b', 'ChildB'):
                gen.Item('b')
        code = gen.generate_str()
        expected = """\
outer = OuterClass(
    name='test',
    child_a=ChildA(
        'a',
    ),
    child_b=ChildB(
        'b',
    ),
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
            with gen.Object('', 'Button').wrap('inline'):
                gen.Item('ok')
        code = gen.generate_str()
        expected = """\
buttons = [
    Button('ok'),
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
    'x',
    y=1,
)
"""
        assert code == expected

    # ── ReprWrapper tests ────────────────────────────────────────────────

    def test_reprwrapper_in_object(self):
        """Item(ReprWrapper) renders a bare expression (no quotes) inside Object."""
        gen = CodeGen()
        with gen.Object('dialog', 'Dialog'):
            gen.Item('static text')
            gen.Item(ReprWrapper('my_var'))
            gen.Item(ReprWrapper('obj.method()'))
        code = gen.generate_str()
        expected = """\
dialog = Dialog(
    'static text',
    my_var,
    obj.method(),
)
"""
        assert code == expected

    def test_reprwrapper_vs_item(self):
        """Item+ReprWrapper vs plain Item."""
        gen = CodeGen()
        with gen.Object('cfg', 'Config'):
            gen.Item('my_var')
            gen.Item(ReprWrapper('my_var'))
        code = gen.generate_str()
        expected = """\
cfg = Config(
    'my_var',
    my_var,
)
"""
        assert code == expected

    def test_reprwrapper_in_list(self):
        """ReprWrapper inside a List context."""
        gen = CodeGen()
        with gen.List('items'):
            gen.Item('static')
            gen.Item(ReprWrapper('dynamic_var'))
        code = gen.generate_str()
        expected = """\
items = [
    'static',
    dynamic_var,
]
"""
        assert code == expected

    def test_reprwrapper_inline_object(self):
        """ReprWrapper with inline-wrapped Object."""
        gen = CodeGen()
        with gen.Object('obj', 'MyClass').wrap('inline'):
            gen.Item(ReprWrapper('x'))
            gen.Item(ReprWrapper('y'))
        code = gen.generate_str()
        assert code == "obj = MyClass(x, y)\n"

    def test_reprwrapper_in_var(self):
        """ReprWrapper as a Var value."""
        gen = CodeGen()
        with gen.Object('cfg', 'Config'):
            gen.Var('name', ReprWrapper('SOME_CONST'))
            gen.Var('count', 42)
        code = gen.generate_str()
        expected = """\
cfg = Config(
    name=SOME_CONST,
    count=42,
)
"""
        assert code == expected

    def test_reprwrapper_in_custom_tab(self):
        """ReprWrapper inside a CustomTab."""
        gen = CodeGen()
        with gen.tab(prefix='lambda: (', suffix=')', line_ending=','):
            gen.Item('x')
            gen.Item(ReprWrapper('y'))
        code = gen.generate_str()
        expected = """\
lambda: (
    'x',
    y,
)
"""
        assert code == expected
