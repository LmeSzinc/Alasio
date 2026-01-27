from alasio.config_dev.format.format_i18n import format_i18n


class TestI18nFormatter:
    """Test cases for i18n formatter function"""

    def test_empty_input(self):
        """Test empty input returns empty string"""
        assert format_i18n(None) == ''
        assert format_i18n('') == ''
        assert format_i18n([]) == ''

    def test_single_line_string(self):
        """Test single line string is stripped and returned as string"""
        assert format_i18n('hello  ') == 'hello'
        assert format_i18n('  world') == '  world'
        assert format_i18n('  test  ') == '  test'

    def test_multi_line_string(self):
        """Test multi-line string is split, stripped and returned as list"""
        input_text = 'line1\nline2  '
        expected = ['line1', 'line2']
        assert format_i18n(input_text) == expected

        input_text = 'line1  \n  line2'
        expected = ['line1', '  line2']
        assert format_i18n(input_text) == expected

    def test_list_input(self):
        """Test list input is processed and returned as list (if multiple items)"""
        input_list = ['line1', 'line2  ']
        expected = ['line1', 'line2']
        assert format_i18n(input_list) == expected

    def test_nested_list(self):
        """Test nested list is flattened"""
        input_list = ['line1', ['line2', 'line3']]
        expected = ['line1', 'line2', 'line3']
        assert format_i18n(input_list) == expected

    def test_mixed_types(self):
        """Test mixed types are converted to string"""
        input_list = ['line1', 123]
        expected = ['line1', '123']
        assert format_i18n(input_list) == expected

    def test_single_item_list_returns_string(self):
        """Test list with single item returns string"""
        input_list = ['hello']
        expected = 'hello'
        assert format_i18n(input_list) == expected

    def test_string_with_empty_lines(self):
        """Test string with empty lines preserves them but strips whitespace"""
        input_text = 'line1\n\nline2'
        expected = ['line1', '', 'line2']
        assert format_i18n(input_text) == expected

    def test_complex_structure(self):
        """Test complex structure with nested lists and newlines"""
        input_data = ['line1', 'line2\nline3', ['line4', 5]]
        expected = ['line1', 'line2', 'line3', 'line4', '5']
        assert format_i18n(input_data) == expected
