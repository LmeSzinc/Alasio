import pytest

from alasio.config_dev.format.extract_method import extract_method, remove_indent


class TestRemoveIndent:
    """Tests for remove_indent helper."""

    def test_remove_one_indent(self):
        """Remove one indent level (4 spaces) from a line."""
        assert remove_indent("    def foo(self):") == "def foo(self):"
        assert remove_indent("        pass") == "    pass"

    def test_remove_multiple_indent(self):
        """Remove multiple indent levels from a line."""
        assert remove_indent("        def foo(self):", indent=2) == "def foo(self):"
        assert remove_indent("            pass", indent=2) == "    pass"
        assert remove_indent("            pass", indent=3) == "pass"

    def test_remove_indent_from_empty_line(self):
        """Empty line should be returned unchanged."""
        assert remove_indent("") == ""
        assert remove_indent("   ") == ""
        assert remove_indent("     ") == ""

    def test_remove_indent_from_line_with_no_leading_space(self):
        """Line with no leading spaces should be returned unchanged."""
        assert remove_indent("foo") == "foo"
        assert remove_indent("def foo():") == "def foo():"

    def test_remove_indent_partial(self):
        """Remove all leading spaces when line has fewer than requested."""
        assert remove_indent("  foo", indent=1) == "foo"
        assert remove_indent(" foo", indent=2) == "foo"

    def test_remove_indent_lines_containing_tabs(self):
        """Tab is considered as an indent and removed."""
        assert remove_indent("\tdef foo(self):") == "def foo(self):"
        line = " \t def foo(self):"
        # indent=1: 1 space removed, tab remains
        assert remove_indent(line) == "\t def foo(self):"

    def test_remove_indent_default_is_one(self):
        """Default indent parameter should be 1."""
        assert remove_indent("    x") == "x"
        assert remove_indent("x") == "x"

    def test_remove_indent_negative_indent(self):
        """Negative indent should be treated as 0, returning line unchanged."""
        assert remove_indent("    x", indent=-1) == "    x"

    def test_remove_indent_zero_indent(self):
        """Zero indent should return the line unchanged."""
        assert remove_indent("    x", indent=0) == "    x"


class TestExtractMethod:
    """Base test cases for basic method extraction."""

    def test_empty_source(self):
        """Extract from empty source should return empty dict."""
        assert extract_method("") == {}
        assert extract_method("\n") == {}
        assert extract_method("   \n") == {}

    def test_no_class(self):
        """Extract from source without any class should return empty dict."""
        source = """x = 1
y = 2


def standalone():
    pass
"""
        assert extract_method(source) == {}

    def test_empty_class(self):
        """Extract from class with no methods should return empty dict."""
        source = """class Empty:
    pass
"""
        assert extract_method(source) == {}

    def test_single_method(self):
        """Extract a single method from a class."""
        source = """class MyClass:
    def method_a(self):
        pass
"""
        result = extract_method(source)
        assert result == {
            "MyClass": {
                "method_a": ["    def method_a(self):", "        pass"]
            }
        }

    def test_multiple_methods(self):
        """Extract multiple methods from one class."""
        source = """class MyClass:
    def method_a(self):
        return 1

    def method_b(self, x):
        return x + 1

    def method_c(self, a, b=10):
        return a * b
"""
        result = extract_method(source)
        assert "MyClass" in result
        assert len(result["MyClass"]) == 3
        assert result["MyClass"]["method_a"] == [
            "    def method_a(self):",
            "        return 1"
        ]
        assert result["MyClass"]["method_b"] == [
            "    def method_b(self, x):",
            "        return x + 1"
        ]
        assert result["MyClass"]["method_c"] == [
            "    def method_c(self, a, b=10):",
            "        return a * b"
        ]

    def test_method_with_multiline_body(self):
        """Extract a method with multiple statements in its body."""
        source = """class Worker:
    def process(self):
        data = self.load()
        result = transform(data)
        self.save(result)
        return result
"""
        result = extract_method(source)
        assert result["Worker"]["process"] == [
            "    def process(self):",
            "        data = self.load()",
            "        result = transform(data)",
            "        self.save(result)",
            "        return result"
        ]

    def test_method_with_all_param_types(self):
        """Extract methods with *args, **kwargs, and default parameters."""
        source = """class SignatureDemo:
    def simple(self):
        pass

    def defaults(self, a, b=1, c="hello"):
        pass

    def var_args(self, *args):
        pass

    def kw_args(self, **kwargs):
        pass

    def mixed(self, a, b=1, *args, **kwargs):
        pass
"""
        result = extract_method(source)
        assert len(result["SignatureDemo"]) == 5
        assert result["SignatureDemo"]["simple"] == [
            "    def simple(self):",
            "        pass"
        ]
        assert result["SignatureDemo"]["defaults"] == [
            '    def defaults(self, a, b=1, c="hello"):',
            "        pass"
        ]
        assert result["SignatureDemo"]["var_args"] == [
            "    def var_args(self, *args):",
            "        pass"
        ]
        assert result["SignatureDemo"]["kw_args"] == [
            "    def kw_args(self, **kwargs):",
            "        pass"
        ]

    def test_return_type_annotation(self):
        """Extract methods with return type annotations."""
        source = """class Typed:
    def get_name(self) -> str:
        return "hello"

    def add(self, x: int, y: int) -> int:
        return x + y
"""
        result = extract_method(source)
        assert result["Typed"]["get_name"] == [
            '    def get_name(self) -> str:',
            '        return "hello"'
        ]
        assert result["Typed"]["add"] == [
            "    def add(self, x: int, y: int) -> int:",
            "        return x + y"
        ]

    def test_multiple_classes(self):
        """Extract methods from multiple classes."""
        source = """class Alpha:
    def foo(self):
        return 1


class Beta:
    def bar(self):
        return 2


class Gamma:
    pass
"""
        result = extract_method(source)
        assert set(result.keys()) == {"Alpha", "Beta"}
        assert "foo" in result["Alpha"]
        assert "bar" in result["Beta"]
        assert "Gamma" not in result  # empty class excluded
        assert len(result) == 2


class TestDecorators:
    """Test cases for methods with decorators."""

    def test_single_decorator(self):
        """Extract a method with a single decorator."""
        source = """class Deco:
    @property
    def name(self):
        return "alice"
"""
        result = extract_method(source)
        assert result["Deco"]["name"] == [
            "    @property",
            "    def name(self):",
            '        return "alice"'
        ]

    def test_multiple_decorators(self):
        """Extract a method with stacked decorators."""
        source = """class MultiDeco:
    @staticmethod
    @lru_cache
    def cached_static():
        return 42
"""
        result = extract_method(source)
        assert result["MultiDeco"]["cached_static"] == [
            "    @staticmethod",
            "    @lru_cache",
            "    def cached_static():",
            "        return 42"
        ]

    def test_decorator_with_args(self):
        """Extract a method with decorator that takes arguments."""
        source = """class Config:
    @deprecated("use new_method instead")
    def old_method(self):
        pass

    @retry(max_attempts=3, delay=1.0)
    def connect(self):
        return "ok"
"""
        result = extract_method(source)
        assert result["Config"]["old_method"] == [
            '    @deprecated("use new_method instead")',
            "    def old_method(self):",
            "        pass"
        ]
        assert result["Config"]["connect"] == [
            "    @retry(max_attempts=3, delay=1.0)",
            "    def connect(self):",
            '        return "ok"'
        ]

    def test_mixed_decorated_and_plain_methods(self):
        """Extract when class has both decorated and plain methods."""
        source = """class Mixed:
    def plain(self):
        pass

    @classmethod
    def create(cls):
        return cls()

    @property
    def computed(self):
        return 1

    async def fetch(self):
        return None
"""
        result = extract_method(source)
        assert len(result["Mixed"]) == 4
        assert result["Mixed"]["plain"] == ["    def plain(self):", "        pass"]
        assert result["Mixed"]["create"] == [
            "    @classmethod",
            "    def create(cls):",
            "        return cls()"
        ]
        assert result["Mixed"]["computed"] == [
            "    @property",
            "    def computed(self):",
            "        return 1"
        ]

    def test_classmethod(self):
        """Extract a @classmethod."""
        source = """class Factory:
    @classmethod
    def from_config(cls, cfg):
        return cls(cfg)
"""
        result = extract_method(source)
        assert result["Factory"]["from_config"] == [
            "    @classmethod",
            "    def from_config(cls, cfg):",
            "        return cls(cfg)"
        ]

    def test_staticmethod(self):
        """Extract a @staticmethod."""
        source = """class Utils:
    @staticmethod
    def clamp(value, low, high):
        return max(low, min(value, high))
"""
        result = extract_method(source)
        assert result["Utils"]["clamp"] == [
            "    @staticmethod",
            "    def clamp(value, low, high):",
            "        return max(low, min(value, high))"
        ]

    def test_decorator_on_same_line(self):
        """Extract with decorator on same line (valid syntax in some cases)."""
        source = """class SameLine:
    @property
    def val(self):
        return 42
"""
        result = extract_method(source)
        assert result["SameLine"]["val"] == [
            "    @property",
            "    def val(self):",
            "        return 42"
        ]


class TestAsyncMethods:
    """Test cases for async methods."""

    def test_basic_async_method(self):
        """Extract a basic async method."""
        source = """class AsyncOps:
    async def fetch_data(self):
        return await api.get()
"""
        result = extract_method(source)
        assert result["AsyncOps"]["fetch_data"] == [
            "    async def fetch_data(self):",
            "        return await api.get()"
        ]

    def test_async_method_with_decorator(self):
        """Extract an async method with decorator."""
        source = """class AsyncService:
    @retry
    async def connect(self):
        return await establish()
"""
        result = extract_method(source)
        assert result["AsyncService"]["connect"] == [
            "    @retry",
            "    async def connect(self):",
            "        return await establish()"
        ]

    def test_multiple_async_methods(self):
        """Extract multiple async methods from one class."""
        source = """class AsyncMix:
    async def get(self):
        return 1

    async def post(self, data):
        return data

    def sync_helper(self):
        return 0
"""
        result = extract_method(source)
        assert len(result["AsyncMix"]) == 3
        assert result["AsyncMix"]["get"] == [
            "    async def get(self):",
            "        return 1"
        ]
        assert result["AsyncMix"]["sync_helper"] == [
            "    def sync_helper(self):",
            "        return 0"
        ]


class TestNestedClassesIgnored:
    """Test that nested/inner classes are NOT extracted."""

    def test_inner_class_not_extracted(self):
        """Inner class methods should not be extracted; only outer class methods."""
        source = """class Outer:
    class Inner:
        def inner_method(self):
            return "inner"

    def outer_method(self):
        return "outer"
"""
        result = extract_method(source)
        assert "Outer" in result
        assert "Inner" not in result
        assert "outer_method" in result["Outer"]
        assert result["Outer"]["outer_method"] == [
            "    def outer_method(self):",
            '        return "outer"'
        ]

    def test_deeply_nested_classes_not_extracted(self):
        """Only root-level classes should be extracted, none of their nested classes."""
        source = """class Level1:
    class Level2:
        class Level3:
            def deepest(self):
                pass

        def mid(self):
            pass

    def top(self):
        pass
"""
        result = extract_method(source)
        assert "Level1" in result
        assert "Level2" not in result
        assert "Level3" not in result
        assert "top" in result["Level1"]

    def test_nested_class_with_decorators_not_extracted(self):
        """Nested class with decorated methods should not be extracted."""
        source = """class Outer:
    class Inner:
        @property
        def value(self):
            return 42

    @classmethod
    def create(cls):
        return cls()
"""
        result = extract_method(source)
        assert "Outer" in result
        assert "Inner" not in result
        assert "create" in result["Outer"]
        assert "value" not in result["Outer"] and "value" not in {k for v in result.values() for k in v}


class TestExactSourcePreservation:
    """Test that extracted source matches original source exactly."""

    def test_preserves_indentation(self):
        """Extracted lines should preserve exact indentation."""
        source = """class Indent:
    def method(self):
        if True:
            for i in range(3):
                print(i)
        return None
"""
        result = extract_method(source)
        expected = [
            "    def method(self):",
            "        if True:",
            "            for i in range(3):",
            "                print(i)",
            "        return None"
        ]
        assert result["Indent"]["method"] == expected

    def test_preserves_empty_lines_within_method(self):
        """Empty lines inside a method body should be preserved."""
        source = """class Spacing:
    def process(self):
        step1()

        step2()

        step3()
        return
"""
        result = extract_method(source)
        assert result["Spacing"]["process"] == [
            "    def process(self):",
            "        step1()",
            "",
            "        step2()",
            "",
            "        step3()",
            "        return"
        ]

    def test_preserves_whitespace_only_empty_lines(self):
        """Blank lines containing only whitespace should be preserved."""
        source = """class Spacing:
    def process(self):
        step1()
   \t
        step2()

        step3()
        return
"""
        result = extract_method(source)
        assert result["Spacing"]["process"] == [
            "    def process(self):",
            "        step1()",
            "   \t",
            "        step2()",
            "",
            "        step3()",
            "        return"
        ]

    def test_preserves_consecutive_empty_lines(self):
        """Multiple consecutive empty lines within a method should be preserved."""
        source = """class Spacing:
    def process(self):
        step1()


        step2()



        step3()
        return
"""
        result = extract_method(source)
        assert result["Spacing"]["process"] == [
            "    def process(self):",
            "        step1()",
            "",
            "",
            "        step2()",
            "",
            "",
            "",
            "        step3()",
            "        return"
        ]

    def test_preserves_empty_line_at_start_of_method_body(self):
        """Empty line right after the def line should be preserved."""
        source = """class Spacing:
    def process(self):

        step1()
        return
"""
        result = extract_method(source)
        assert result["Spacing"]["process"] == [
            "    def process(self):",
            "",
            "        step1()",
            "        return"
        ]

    def test_preserves_comment_lines(self):
        """Comments inside a method should be preserved."""
        source = """class Docs:
    def parse(self, data):
        # validate input
        if not data:
            return None
        # process
        result = transform(data)
        return result
"""
        result = extract_method(source)
        assert "        # validate input" in result["Docs"]["parse"]
        assert "        # process" in result["Docs"]["parse"]

    def test_string_contents_preserved(self):
        """String literals including quotes and special chars preserved."""
        source = """class Strings:
    def greet(self, name):
        return f"Hello, {name}!"

    def raw_str(self):
        path = r"C:\\Users\\test"
        return path
"""
        result = extract_method(source)
        assert '        return f"Hello, {name}!"' in result["Strings"]["greet"]
        assert '        path = r"C:\\Users\\test"' in result["Strings"]["raw_str"]

    def test_multiline_string_inside_method(self):
        """Multi-line string literals within a method should be preserved."""
        source = """class MultiLine:
    def docstring(self):
        \"\"\"A multi-line
        docstring.
        \"\"\"
        return True
"""
        result = extract_method(source)
        expected = [
            '    def docstring(self):',
            '        """A multi-line',
            '        docstring.',
            '        """',
            '        return True'
        ]
        assert result["MultiLine"]["docstring"] == expected


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_class_body_has_only_non_function_nodes(self):
        """Class with only assignments and other non-method nodes."""
        source = """class Constants:
    PI = 3.14159
    E = 2.71828
    NAME: str = "const"
"""
        result = extract_method(source)
        assert result == {}

    def test_class_with_mixed_data_and_methods(self):
        """Class with class variables, properties, and methods."""
        source = """class Model:
    default_limit = 100

    def __init__(self, name):
        self.name = name

    label = "model"

    def get_name(self):
        return self.name
"""
        result = extract_method(source)
        assert len(result["Model"]) == 2
        assert "__init__" in result["Model"]
        assert "get_name" in result["Model"]

    def test_dunder_methods(self):
        """Extract special methods like __init__, __repr__, etc."""
        source = """class Special:
    def __init__(self, x):
        self.x = x

    def __repr__(self):
        return f"Special({self.x})"

    def __eq__(self, other):
        return self.x == other.x
"""
        result = extract_method(source)
        assert len(result["Special"]) == 3
        assert "__init__" in result["Special"]
        assert "__repr__" in result["Special"]
        assert "__eq__" in result["Special"]

    def test_one_liner_methods(self):
        """Extract one-line methods."""
        source = """class Compact:
    def get_x(self): return 1
    def get_y(self): return 2
"""
        result = extract_method(source)
        assert result["Compact"]["get_x"] == ["    def get_x(self): return 1"]
        assert result["Compact"]["get_y"] == ["    def get_y(self): return 2"]

    def test_lambda_not_confused_with_method(self):
        """Lambda assignments should not be extracted as methods."""
        source = """class NoLambda:
    add = lambda a, b: a + b

    def real_method(self):
        pass
"""
        result = extract_method(source)
        assert len(result["NoLambda"]) == 1
        assert "real_method" in result["NoLambda"]
        assert "add" not in result["NoLambda"]

    def test_nested_functions_inside_method(self):
        """Inner/nested functions inside a method should be part of the method body."""
        source = """class Nested:
    def outer(self):
        def inner():
            return 1
        return inner()
"""
        result = extract_method(source)
        assert result["Nested"]["outer"] == [
            "    def outer(self):",
            "        def inner():",
            "            return 1",
            "        return inner()"
        ]

    def test_extra_indented_method(self):
        """Extract when the whole method has extra indentation."""
        source = """class ExtraIndent:
        def method(self):
            return 1
"""
        result = extract_method(source)
        assert result["ExtraIndent"]["method"] == [
            "        def method(self):",
            "            return 1"
        ]

    def test_windows_line_endings(self):
        """Handle \\r\\n line endings correctly."""
        source = "class Win:\r\n    def method(self):\r\n        pass\r\n"
        result = extract_method(source)
        expected = ["    def method(self):", "        pass"]
        assert result["Win"]["method"] == expected

    def test_syntax_error_raises(self):
        """SyntaxError should propagate when source code is invalid."""
        with pytest.raises(SyntaxError):
            extract_method("class Broken:")
        with pytest.raises(SyntaxError):
            extract_method("def unclosed_paren(:")
        with pytest.raises(SyntaxError):
            extract_method("x = ")
        with pytest.raises(SyntaxError):
            extract_method("class A:\n  def method(self)::")
        with pytest.raises(SyntaxError):
            extract_method("""class A:
    def ok(self):
        pass

    this is not valid python
""")


class TestKeepIndent:
    """Tests for extract_method with keep_indent parameter."""

    def test_keep_indent_true_by_default(self):
        """keep_indent should be True by default."""
        source = """class A:
    def foo(self):
        pass
"""
        result = extract_method(source)
        assert result["A"]["foo"] == ["    def foo(self):", "        pass"]
        # Explicit keep_indent=True should match default
        result_explicit = extract_method(source, keep_indent=True)
        assert result == result_explicit

    def test_keep_indent_false_removes_one_level(self):
        """keep_indent=False should remove one indent level from each line."""
        source = """class A:
    def foo(self):
        pass
"""
        result = extract_method(source, keep_indent=False)
        assert result["A"]["foo"] == ["def foo(self):", "    pass"]

    def test_keep_indent_false_with_decorators(self):
        """Decorator lines should also be dedented."""
        source = """class A:
    @property
    def name(self):
        return "hello"
"""
        result = extract_method(source, keep_indent=False)
        assert result["A"]["name"] == [
            "@property",
            "def name(self):",
            '    return "hello"'
        ]

    def test_keep_indent_false_empty_lines(self):
        """Empty/blank lines should remain unchanged."""
        source = """class A:
    def process(self):
        step1()

        step2()
        return
"""
        result = extract_method(source, keep_indent=False)
        assert result["A"]["process"] == [
            "def process(self):",
            "    step1()",
            "",
            "    step2()",
            "    return"
        ]

    def test_keep_indent_false_multiple_classes(self):
        """Multiple classes should all be dedented."""
        source = """class A:
    def foo(self):
        return 1

class B:
    @classmethod
    def bar(cls):
        return 2
"""
        result = extract_method(source, keep_indent=False)
        assert result["A"]["foo"] == [
            "def foo(self):",
            "    return 1"
        ]
        assert result["B"]["bar"] == [
            "@classmethod",
            "def bar(cls):",
            "    return 2"
        ]

    def test_keep_indent_false_with_async(self):
        """Async methods should be dedented correctly."""
        source = """class A:
    async def fetch(self):
        return await api()
"""
        result = extract_method(source, keep_indent=False)
        assert result["A"]["fetch"] == [
            "async def fetch(self):",
            "    return await api()"
        ]

    def test_keep_indent_false_one_liner(self):
        """Single-line methods should be dedented."""
        source = """class A:
    def foo(self): return 1
    def bar(self): return 2
"""
        result = extract_method(source, keep_indent=False)
        assert result["A"]["foo"] == ["def foo(self): return 1"]
        assert result["A"]["bar"] == ["def bar(self): return 2"]

    def test_keep_indent_false_multiline_string(self):
        """Multiline strings inside methods should have consistent dedent."""
        source = """class A:
    def docstring(self):
        \"\"\"
        A multiline docstring.

        Second paragraph.
        \"\"\"
        return True
"""
        result = extract_method(source, keep_indent=False)
        expected = [
            "def docstring(self):",
            '    """',
            "    A multiline docstring.",
            "",
            "    Second paragraph.",
            '    """',
            "    return True"
        ]
        assert result["A"]["docstring"] == expected

    def test_keep_indent_false_nested_func(self):
        """Nested functions inside method should have one level removed."""
        source = """class A:
    def outer(self):
        def inner():
            return 1
        return inner()
"""
        result = extract_method(source, keep_indent=False)
        assert result["A"]["outer"] == [
            "def outer(self):",
            "    def inner():",
            "        return 1",
            "    return inner()"
        ]


class TestExtractMethodResultType:
    """Test the structure and types of the returned dict."""

    def test_top_level_keys_are_class_names(self):
        """Top-level dict keys should be class names (strings)."""
        source = """class A:
    def foo(self): pass
class B:
    def bar(self): pass
"""
        result = extract_method(source)
        assert set(result.keys()) == {"A", "B"}
        assert all(isinstance(k, str) for k in result)

    def test_nested_keys_are_method_names(self):
        """Second-level dict keys should be method names (strings)."""
        source = """class A:
    def foo(self): pass
    def bar(self): pass
"""
        result = extract_method(source)
        assert set(result["A"].keys()) == {"foo", "bar"}

    def test_values_are_list_of_strings(self):
        """Each value should be a list of strings (source lines)."""
        source = """class A:
    def foo(self):
        return 1
"""
        result = extract_method(source)
        lines = result["A"]["foo"]
        assert isinstance(lines, list)
        assert all(isinstance(line, str) for line in lines)
        assert len(lines) == 2

    def test_result_is_dict(self):
        """Top-level return value should be a dict."""
        source = """class A:
    def foo(self): pass
"""
        result = extract_method(source)
        assert isinstance(result, dict)
