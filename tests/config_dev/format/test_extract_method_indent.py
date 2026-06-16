import pytest

from alasio.config_dev.format.extract_method import remove_indent


class TestRemoveIndentBasic:
    """Basic indent removal: default indent=1, each level is 4 spaces."""

    def test_remove_one_level_simple(self):
        """Remove one indent from code lines."""
        assert remove_indent("    pass") == "pass"
        assert remove_indent("    return x") == "return x"
        assert remove_indent("    if True:") == "if True:"

    def test_remove_one_level_preserves_inner_indent(self):
        """Lines with deeper indentation retain relative depth after dedent."""
        assert remove_indent("        pass") == "    pass"
        assert remove_indent("            return x") == "        return x"
        assert remove_indent("        if True:") == "    if True:"

    def test_remove_one_level_from_def(self):
        """Function/method definitions should have one indent removed."""
        assert remove_indent("    def foo(self):") == "def foo(self):"
        assert remove_indent("    async def fetch(self):") == "async def fetch(self):"
        assert remove_indent("    def foo(self, a, b=1):") == "def foo(self, a, b=1):"

    def test_remove_one_level_from_decorator(self):
        """Decorator lines should have one indent removed."""
        assert remove_indent("    @property") == "@property"
        assert remove_indent("    @classmethod") == "@classmethod"
        assert remove_indent("    @app.route('/')") == "@app.route('/')"

    def test_remove_one_level_from_comment(self):
        """Comment lines should have one indent removed."""
        assert remove_indent("    # this is a comment") == "# this is a comment"
        assert remove_indent("        # nested comment") == "    # nested comment"

    def test_remove_one_level_from_string(self):
        """String content lines should have one indent removed."""
        assert remove_indent('    """docstring"""') == '"""docstring"""'
        assert remove_indent("    \"\"\"docstring\"\"\"") == "\"\"\"docstring\"\"\""
        assert remove_indent("    'hello world'") == "'hello world'"


class TestRemoveIndentMultiple:
    """Remove multiple indent levels at once."""

    def test_remove_two_levels(self):
        """Remove 2 indent levels (8 spaces)."""
        assert remove_indent("        pass", indent=2) == "pass"
        assert remove_indent("            return x", indent=2) == "    return x"
        assert remove_indent("        def foo(self):", indent=2) == "def foo(self):"

    def test_remove_three_levels(self):
        """Remove 3 indent levels (12 spaces)."""
        assert remove_indent("            pass", indent=3) == "pass"
        assert remove_indent("                return x", indent=3) == "    return x"
        assert remove_indent("            def foo(self):", indent=3) == "def foo(self):"

    def test_remove_four_levels(self):
        """Remove 4 indent levels (16 spaces)."""
        assert remove_indent("                pass", indent=4) == "pass"
        assert remove_indent("                    return x", indent=4) == "    return x"

    def test_remove_varying_indent_from_different_sources(self):
        """Varying original indents all produce same dedented output."""
        assert remove_indent("    pass", indent=1) == "pass"
        assert remove_indent("        pass", indent=2) == "pass"
        assert remove_indent("            pass", indent=3) == "pass"
        assert remove_indent("                pass", indent=4) == "pass"


class TestRemoveIndentEdgeCases:
    """Edge cases: boundary values, special inputs, error conditions."""

    def test_empty_string(self):
        """Empty string should return empty string."""
        assert remove_indent("") == ""

    def test_only_spaces(self):
        """Strings with only spaces should become empty when indent > 0."""
        assert remove_indent(" ") == ""
        assert remove_indent("  ") == ""
        assert remove_indent("   ") == ""
        assert remove_indent("    ") == ""
        assert remove_indent("     ") == ""
        assert remove_indent("          ") == ""

    def test_only_tabs(self):
        """Strings with only tabs should become empty."""
        assert remove_indent("\t") == ""
        assert remove_indent("\t\t") == ""
        assert remove_indent("\t\t\t") == ""

    def test_mixed_whitespace_only(self):
        """Strings with mixed whitespace only should become empty."""
        assert remove_indent(" \t ") == ""
        assert remove_indent("  \t  ") == ""

    def test_no_leading_whitespace(self):
        """Lines with no leading whitespace should be returned unchanged."""
        assert remove_indent("pass") == "pass"
        assert remove_indent("return x") == "return x"
        assert remove_indent("x = 1") == "x = 1"
        assert remove_indent("") == ""

    def test_indent_not_multiple_of_four(self):
        """Indent amount not aligned to 4-space boundary."""
        # 2 spaces, indent=1 removes 4 spaces, but only 2 available
        assert remove_indent("  pass", indent=1) == "pass"
        # 6 spaces, indent=1 removes 4 spaces
        assert remove_indent("      return x", indent=1) == "  return x"
        # 6 spaces, indent=2 removes 8 spaces, but only 6 available
        assert remove_indent("      pass", indent=2) == "pass"
        # 3 spaces, indent=1 removes 4 spaces, but only 3 available
        assert remove_indent("   pass", indent=1) == "pass"

    def test_leading_spaces_exactly_at_boundary(self):
        """Leading spaces exactly matching the removal width."""
        # Exactly 4 spaces, indent=1 removes 4
        assert remove_indent("    x", indent=1) == "x"
        # Exactly 8 spaces, indent=2 removes 8
        assert remove_indent("        x", indent=2) == "x"
        # Exactly 12 spaces, indent=3 removes 12
        assert remove_indent("            x", indent=3) == "x"

    def test_leading_spaces_one_more_than_boundary(self):
        """Leading spaces one more than the removal width."""
        # 5 spaces, indent=1 removes 4, 1 remains
        assert remove_indent("     x", indent=1) == " x"
        # 9 spaces, indent=2 removes 8, 1 remains
        assert remove_indent("         x", indent=2) == " x"
        # 13 spaces, indent=3 removes 12, 1 remains
        assert remove_indent("             x", indent=3) == " x"

    def test_newline_characters_not_in_line(self):
        """remove_indent should work on single lines; newlines should not appear."""
        assert remove_indent("    pass\n") == "pass\n"
        assert remove_indent("    pass\r\n") == "pass\r\n"

    def test_line_with_only_newline(self):
        """A line containing only a newline should be treated as blank."""
        assert remove_indent("\n") == ""
        assert remove_indent("\r\n") == ""

    def test_zero_indent(self):
        """Indent=0 should return the line unchanged."""
        assert remove_indent("    x", indent=0) == "    x"
        assert remove_indent("pass", indent=0) == "pass"
        assert remove_indent("", indent=0) == ""

    def test_negative_indent(self):
        """Negative indent should return the line unchanged."""
        assert remove_indent("    x", indent=-1) == "    x"
        assert remove_indent("    x", indent=-5) == "    x"
        assert remove_indent("pass", indent=-100) == "pass"

    def test_very_large_indent(self):
        """Very large indent should remove all leading spaces."""
        assert remove_indent("    x", indent=100) == "x"
        assert remove_indent(" " * 100 + "x", indent=100) == "x"
        assert remove_indent(" " * 200 + "x", indent=50) == "x"


class TestRemoveIndentWithTabs:
    """Handling of tabs in input lines."""

    def test_tab_as_one_indent_level(self):
        """A single tab is removed as one indent level."""
        assert remove_indent("\tpass") == "pass"
        assert remove_indent("\t\tpass", indent=2) == "pass"

    def test_tab_removes_first_then_spaces(self):
        """Tabs are consumed first per indent level before spaces."""
        assert remove_indent("\t    pass") == "    pass"
        assert remove_indent("\t    pass", indent=2) == "pass"
        assert remove_indent("\t        pass", indent=1) == "        pass"
        assert remove_indent("\t        pass", indent=2) == "    pass"

    def test_mixed_spaces_before_tab(self):
        """Spaces before a tab are removed as part of an indent level."""
        assert remove_indent("  \tpass") == "\tpass"
        assert remove_indent("    \tpass") == "\tpass"
        assert remove_indent("        \tpass", indent=2) == "\tpass"

    def test_tab_then_spaces(self):
        """Tab then spaces: tab is removed first, then spaces."""
        assert remove_indent("\t  pass") == "  pass"
        assert remove_indent("\t    pass", indent=2) == "pass"

    def test_tab_at_greater_indent(self):
        """Multiple tabs with large indent removal."""
        assert remove_indent("\t\tpass") == "\tpass"
        assert remove_indent("\t\t\tpass") == "\t\tpass"
        assert remove_indent("\t\t\tpass", indent=3) == "pass"

    def test_tab_only_blank_line(self):
        """Lines that are only tabs should become empty."""
        assert remove_indent("\t") == ""
        assert remove_indent("\t\t") == ""


class TestRemoveIndentSpecialContent:
    """Lines with special/unusual content."""

    def test_unicode_characters(self):
        """Unicode characters should be preserved."""
        assert remove_indent("    def 函数():") == "def 函数():"
        assert remove_indent('        return \u201c\u4f60\u597d\u201d') == '    return \u201c\u4f60\u597d\u201d'
        assert remove_indent("    # 中文注释") == "# 中文注释"
        assert remove_indent("    café = 1") == "café = 1"

    def test_special_characters(self):
        """Lines with special characters should be preserved."""
        assert remove_indent("    a += b") == "a += b"
        assert remove_indent("    a @= b") == "a @= b"
        assert remove_indent("    a := b") == "a := b"
        assert remove_indent("    a **= b") == "a **= b"

    def test_string_literals_with_leading_space(self):
        """String literals with leading spaces inside a dedented block."""
        assert remove_indent("    \"  indented string\"") == "\"  indented string\""
        assert remove_indent('    \'  indented string\'') == '\'  indented string\''

    def test_backslash_continuation(self):
        """Lines with backslash continuation."""
        assert remove_indent("    x = a + \\") == "x = a + \\"
        assert remove_indent("        + b") == "    + b"

    def test_empty_body_line(self):
        """A line that is just 'pass' should be dedented."""
        assert remove_indent("    pass") == "pass"
        assert remove_indent("    ...") == "..."
        assert remove_indent("    pass  # noop") == "pass  # noop"

    def test_line_with_trailing_spaces(self):
        """Trailing spaces should be preserved."""
        assert remove_indent("    x   ") == "x   "
        assert remove_indent("    x  # comment   ") == "x  # comment   "

    def test_line_with_only_trailing_spaces(self):
        """A line with only trailing spaces (after leading spaces) that is otherwise empty."""
        # "    " → empty
        assert remove_indent("    ") == ""
        # "    " with indent=1 → empty (handled by the iss pace check)
        assert remove_indent("      ") == ""

    def test_non_breaking_space_not_stripped(self):
        """\\xa0 (non-breaking space) should not be treated as a regular space."""
        line = "\xa0" * 4 + "x"
        assert remove_indent(line) == line  # \xa0 is not lstrip(' ') so stays
        line = "\xa0" + "pass"
        assert remove_indent(line) == line


class TestRemoveIndentConsistency:
    """Ensure deterministic and consistent behavior across repeated calls."""

    def test_idempotent_for_already_dedented(self):
        """Calling remove_indent multiple times on already-dedented lines should
        produce the same result each time (no further changes)."""
        line = "def foo():"
        once = remove_indent(line)
        twice = remove_indent(once)
        assert once == "def foo():"
        assert twice == "def foo():"

    def test_idempotent_for_empty_lines(self):
        """Calling remove_indent on empty lines repeatedly should stay empty."""
        assert remove_indent(remove_indent("")) == ""
        assert remove_indent(remove_indent("   ")) == ""
        assert remove_indent(remove_indent("\t")) == ""

    def test_double_dedent_equals_single_larger_dedent(self):
        """Removing 1 indent twice should equal removing 2 indent once."""
        line = "            pass"
        assert remove_indent(remove_indent(line)) == remove_indent(line, indent=2)
        line = "                return x"
        assert remove_indent(remove_indent(line, indent=2), indent=2) == remove_indent(line, indent=4)

    def test_zero_and_one_indent_consistency(self):
        """Indent=0 then indent=1 should equal indent=1 alone."""
        line = "    x"
        assert remove_indent(remove_indent(line, indent=0), indent=1) == remove_indent(line, indent=1)


class TestRemoveIndentNonSpaceCharacters:
    """Leading non-space characters that resemble indentation."""

    def test_leading_stars(self):
        """Lines starting with * (e.g. bullet lists in docstrings) unchanged."""
        assert remove_indent("    * item") == "* item"
        assert remove_indent("        * nested") == "    * nested"

    def test_leading_numbers(self):
        """Lines starting with numbers unchanged."""
        assert remove_indent("    1. first") == "1. first"
        assert remove_indent("    42. answer") == "42. answer"

    def test_leading_dash(self):
        """Lines starting with - unchanged."""
        assert remove_indent("    - item") == "- item"
        assert remove_indent("        - nested") == "    - nested"

    def test_leading_gt(self):
        """Lines starting with > (e.g. quotes in docstrings) unchanged."""
        assert remove_indent("    > quote") == "> quote"


class TestRemoveIndentFuzz:
    """Fuzz-style tests with random-like inputs."""

    @pytest.mark.parametrize("spaces,content,indent_level,expected", [
        # Exactly 4 spaces
        (4, "pass", 1, "pass"),
        (4, "x = 1", 1, "x = 1"),
        # Exactly 8 spaces
        (8, "pass", 1, "    pass"),
        (8, "pass", 2, "pass"),
        # Extra spaces beyond
        (5, "pass", 1, " pass"),
        (9, "pass", 2, " pass"),
        # Fewer spaces than indent
        (2, "pass", 1, "pass"),
        (6, "pass", 2, "pass"),
        # Zero indent
        (4, "pass", 0, "    pass"),
        (0, "pass", 1, "pass"),
    ])
    def test_parametrized_spaces(self, spaces, content, indent_level, expected):
        """Parametrized combinations of leading spaces, content, and indent level."""
        line = " " * spaces + content
        assert remove_indent(line, indent=indent_level) == expected

    @pytest.mark.parametrize("line,indent_level,expected", [
        # Empty / blank
        ("", 1, ""),
        (" ", 1, ""),
        ("  ", 1, ""),
        ("   ", 1, ""),
        ("    ", 1, ""),
        # Single characters
        ("    a", 1, "a"),
        ("        a", 2, "a"),
        ("            a", 3, "a"),
        # Edge: tabs
        ("\t", 1, ""),
        ("\t ", 1, ""),
        (" \t", 1, ""),
        # Edge: non-ASCII
        ("    \u4e2d\u6587", 1, "\u4e2d\u6587"),
        # Very small spaces
        ("   a", 1, "a"),
        ("  a", 1, "a"),
        (" a", 1, "a"),
    ])
    def test_parametrized_edge(self, line, indent_level, expected):
        """Parametrized edge cases for remove_indent."""
        assert remove_indent(line, indent=indent_level) == expected


class TestRemoveIndentNegativeBehavior:
    """Behavior under negative, zero, and extreme indent values."""

    def test_negative_indent_down_to_zero(self):
        """A chain from -5 up to 0 should all return unchanged."""
        line = "    x"
        for i in range(-5, 1):
            assert remove_indent(line, indent=i) == "    x", f"Failed at indent={i}"

    def test_float_indent_triggers_type_error_on_slice(self):
        """Float indent should cause TypeError when slicing with float index."""
        with pytest.raises(TypeError):
            remove_indent("        x", indent=1.5)
        with pytest.raises(TypeError):
            remove_indent("            x", indent=1.5)
