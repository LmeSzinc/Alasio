from alasio.codegen.python.gen import CodeGen


class TestObjRaw:
    def test_raw_indent_true_default(self):
        """Raw with indent=True (default) should cleandoc and add generator indent."""
        gen = CodeGen()
        with gen.Class('MyClass'):
            gen.Raw("""
                def hello():
                    return "world"
            """)

        code = gen.generate_str()
        expected = """\
class MyClass:
    def hello():
        return "world"
"""
        assert code == expected

    def test_raw_indent_false(self):
        """Raw with indent=False should keep input indent, not add extra indent."""
        gen = CodeGen()
        gen.Raw("""
        def hello():
            return "world"
""", indent=False)

        code = gen.generate_str()
        expected = """\
        def hello():
            return "world"
"""
        assert code == expected

    def test_raw_indent_false_inside_class(self):
        """Raw with indent=False inside a class block should not add class indent."""
        gen = CodeGen()
        with gen.Class('MyClass'):
            gen.Raw("""
            def hello():
                return "world"
            """, indent=False)

        code = gen.generate_str()
        expected = """\
class MyClass:
            def hello():
                return "world"
"""
        assert code == expected

    def test_raw_single_line(self):
        """Raw with a single line and indent=True."""
        gen = CodeGen()
        gen.Raw('print(1)')

        code = gen.generate_str()
        assert code == 'print(1)\n'

    def test_raw_single_line_indent_false(self):
        """Raw with a single line and indent=False."""
        gen = CodeGen()
        gen.Raw('print(1)', indent=False)

        code = gen.generate_str()
        assert code == 'print(1)\n'

    def test_raw_empty_text(self):
        """Raw with empty text should produce nothing."""
        gen = CodeGen()
        gen.Raw('')

        code = gen.generate_str()
        assert code == ''

    def test_raw_empty_text_indent_false(self):
        """Raw with empty text and indent=False should produce nothing."""
        gen = CodeGen()
        gen.Raw('', indent=False)

        code = gen.generate_str()
        assert code == ''

    def test_raw_multiline_no_extra_leading_trailing(self):
        """Raw should strip leading/trailing empty lines regardless of indent mode."""
        gen = CodeGen()
        gen.Raw("""


            line1

            line2


        """)

        code = gen.generate_str()
        expected = """\
line1

line2
"""
        assert code == expected

    def test_raw_indent_false_leading_trailing_stripped(self):
        """Raw with indent=False should still strip leading/trailing empty lines."""
        gen = CodeGen()
        gen.Raw("""


            keep this
            and this


        """, indent=False)

        code = gen.generate_str()
        expected = """\
            keep this
            and this
"""
        assert code == expected

    def test_raw_indent_true_inside_def(self):
        """Raw with indent=True inside a function should get additional indent."""
        gen = CodeGen()
        with gen.Def('run'):
            gen.Raw("""
                print("working")
            """)

        code = gen.generate_str()
        expected = """\
def run():
    print("working")
"""
        assert code == expected

    def test_raw_indent_false_inside_def(self):
        """Raw with indent=False inside a function should keep input indent."""
        gen = CodeGen()
        with gen.Def('run'):
            gen.Raw("""
                print("working")
            """, indent=False)

        code = gen.generate_str()
        expected = """\
def run():
                print("working")
"""
        assert code == expected

    def test_raw_indent_true_nested_context(self):
        """Raw with indent=True inside nested contexts."""
        gen = CodeGen()
        with gen.Class('Outer'):
            with gen.Def('method'):
                gen.Raw("""
                    pass
                """)

        code = gen.generate_str()
        expected = """\
class Outer:
    def method():
        pass
"""
        assert code == expected

    def test_raw_indent_false_nested_context(self):
        """Raw with indent=False inside nested contexts should not add indent."""
        gen = CodeGen()
        with gen.Class('Outer'):
            with gen.Def('method'):
                gen.Raw("""
                    pass
                """, indent=False)

        code = gen.generate_str()
        expected = """\
class Outer:
    def method():
                    pass
"""
        assert code == expected

    def test_raw_preserves_inner_relative_indent(self):
        """Raw with indent=True should properly dedent and re-indent."""
        gen = CodeGen()
        with gen.Def('foo'):
            gen.Raw("""
                if True:
                    print("yes")
                else:
                    print("no")
            """)

        code = gen.generate_str()
        expected = """\
def foo():
    if True:
        print("yes")
    else:
        print("no")
"""
        assert code == expected

    def test_raw_indent_false_preserves_all_indent(self):
        """Raw with indent=False should preserve all original indentation."""
        gen = CodeGen()
        gen.Raw("""\
    level1
        level2
            level3
""", indent=False)

        code = gen.generate_str()
        expected = """\
    level1
        level2
            level3
"""
        assert code == expected

    def test_raw_no_indent_in_input(self):
        """Raw with input that has no leading indent."""
        gen = CodeGen()
        with gen.Def('foo'):
            gen.Raw('x = 1')

        code = gen.generate_str()
        expected = """\
def foo():
    x = 1
"""
        assert code == expected

    def test_raw_no_indent_in_input_indent_false(self):
        """Raw indent=False with input that has no leading indent."""
        gen = CodeGen()
        with gen.Def('foo'):
            gen.Raw('x = 1', indent=False)

        code = gen.generate_str()
        assert code == 'def foo():\nx = 1\n'
