import json
from datetime import datetime

import pytest

from alasio.ext.file.jsonfile import NoIndent, NoIndentNoSpace, json_dumps_custom_indent


class TestJsonDumpsCustomIndent:
    """Test cases for json_dumps_custom_indent function"""

    def test_basic_noindent_functionality(self):
        """Test 1: Basic NoIndent functionality"""
        data = {
            'name': 'test',
            'coordinates': NoIndent([10, 20, 30, 40]),
            'description': 'This is a test'
        }

        result = json_dumps_custom_indent(data)

        # Should return bytes
        assert isinstance(result, bytes), "Function should return bytes"

        # NoIndent should be on one line
        assert b'"coordinates": [10, 20, 30, 40]' in result
        # Regular content should be properly indented
        assert b'{\n  "name": "test"' in result
        assert b'  "description": "This is a test"\n}' in result

    def test_basic_noindent_nospace_functionality(self):
        """Test 1: Basic NoIndentNoSpace functionality"""
        data = {
            'name': 'test',
            'color': NoIndentNoSpace([255, 128, 64]),
            'metadata': {'version': 1}
        }

        result = json_dumps_custom_indent(data)

        # Should return bytes
        assert isinstance(result, bytes), "Function should return bytes"

        # NoIndentNoSpace should be on one line without spaces
        assert b'"color": [255,128,64]' in result
        # Regular content should be properly indented with spaces
        assert b'  "metadata": {\n    "version": 1\n  }' in result

    def test_parent_indentation_no_effect(self):
        """Test 2: Parent indentation should not affect NoIndent/NoIndentNoSpace"""
        data = {
            'level1': {
                'level2': {
                    'level3': {
                        'noindent_array': NoIndent([1, 2, 3, 4]),
                        'nospace_array': NoIndentNoSpace(['a', 'b', 'c']),
                        'normal_data': 'should be indented'
                    }
                }
            }
        }

        result = json_dumps_custom_indent(data)

        # Deep nesting should not affect NoIndent behavior
        assert b'"noindent_array": [1, 2, 3, 4]' in result
        assert b'"nospace_array": ["a","b","c"]' in result

        # Normal content should still be properly nested (8 spaces for level 3)
        assert b'        "normal_data": "should be indented"' in result

    def test_replacement_no_side_effects(self):
        """Test 3: Replacement should not affect other content"""
        # Use strings that might contain patterns similar to placeholders
        data = {
            'suspicious_string': 'No|1NdEnτ-12345 and τ characters',
            'array_with_noindent': NoIndent([1, 2, 3]),
            'another_string': 'Contains placeholder-like text No|1NdEnτ-n0SpaCæ-67890',
            'normal_array': [4, 5, 6]
        }

        result = json_dumps_custom_indent(data)

        # Original suspicious strings should remain unchanged
        data = '"suspicious_string": "No|1NdEnτ-12345 and τ characters"'
        assert data.encode('utf-8') in result
        data = '"another_string": "Contains placeholder-like text No|1NdEnτ-n0SpaCæ-67890"'
        assert data.encode('utf-8') in result

        # NoIndent should work correctly
        assert b'"array_with_noindent": [1, 2, 3]' in result

        # Normal array should be indented
        assert b'  "normal_array": [\n    4,\n    5,\n    6\n  ]' in result

    def test_nested_noindent_usage(self):
        """Test 4: Nested usage of NoIndent/NoIndentNoSpace"""
        data = {
            'outer': NoIndent({
                'inner_normal': [1, 2, 3],
                'inner_noindent': NoIndent(['a', 'b', 'c']),
                'inner_nospace': NoIndentNoSpace({'x': 1, 'y': 2})
            }),
            'mixed_nesting': {
                'level1': NoIndentNoSpace([
                    NoIndent({'nested': True}),
                    'normal_string'
                ])
            }
        }

        result = json_dumps_custom_indent(data)

        # Check that outer NoIndent contains the expected content
        assert b'"outer": ' in result

        # Check that nested NoIndent objects are properly handled
        # The outer object should be compacted but inner NoIndent items should still work
        result_str = result.decode('utf-8')
        assert 'inner_noindent' in result_str
        assert 'inner_nospace' in result_str

        # Verify the mixed nesting structure
        assert b'"mixed_nesting"' in result
        assert b'"level1":' in result

    def test_default_parameter_propagation(self):
        """Test 5: default=str and other parameters should propagate to inline content"""
        # Test with datetime objects that need custom serialization
        test_date = datetime(2023, 12, 25, 10, 30, 45)

        data = {
            'regular_date': test_date,
            'noindent_dates': NoIndent([test_date, test_date]),
            'nospace_date_dict': NoIndentNoSpace({
                'created': test_date,
                'modified': test_date
            })
        }

        result = json_dumps_custom_indent(data)

        # All datetime objects should be serialized as strings
        date_str = str(test_date).encode('utf-8')
        assert date_str in result

        # Check that the default function was applied to inline content too
        # NoIndent array should contain string-serialized dates
        noindent_pattern = b'"noindent_dates": ["' + str(test_date).encode('utf-8') + b'", "' + str(test_date).encode(
            'utf-8') + b'"]'
        assert noindent_pattern in result

        # NoIndentNoSpace should also have properly serialized dates (without spaces)
        nospace_pattern = b'"nospace_date_dict": {"created":"' + str(test_date).encode(
            'utf-8') + b'","modified":"' + str(test_date).encode('utf-8') + b'"}'
        assert nospace_pattern in result

    def test_empty_and_edge_cases(self):
        """Test edge cases: empty objects, None values, etc."""
        data = {
            'empty_noindent': NoIndent([]),
            'empty_nospace': NoIndentNoSpace({}),
            'none_in_noindent': NoIndent([None, 'test', None]),
            'complex_nested': {
                'data': NoIndent({
                    'empty_list': [],
                    'empty_dict': {},
                    'none_value': None
                })
            }
        }

        result = json_dumps_custom_indent(data)

        # Empty structures should work correctly
        assert b'"empty_noindent": []' in result
        assert b'"empty_nospace": {}' in result

        # None values should be preserved
        assert b'"none_in_noindent": [null, "test", null]' in result

    def test_mixed_usage_comprehensive(self):
        """Comprehensive test mixing all features"""
        data = {
            'config': {
                'window_size': NoIndent([1920, 1080]),
                'colors': {
                    'primary': NoIndentNoSpace([255, 0, 0]),
                    'secondary': NoIndentNoSpace([0, 255, 0]),
                    'palette': NoIndent([
                        NoIndentNoSpace([100, 100, 100]),
                        NoIndentNoSpace([200, 200, 200])
                    ])
                },
                'settings': {
                    'nested': {
                        'deep': {
                            'coordinates': NoIndent([10, 20, 30, 40])
                        }
                    }
                }
            },
            'metadata': NoIndent({
                'version': '1.0',
                'author': 'test',
                'tags': NoIndentNoSpace(['tag1', 'tag2', 'tag3'])
            })
        }

        result = json_dumps_custom_indent(data)

        # Validate various patterns
        assert b'"window_size": [1920, 1080]' in result
        assert b'"primary": [255,0,0]' in result
        assert b'"secondary": [0,255,0]' in result

        # Nested NoIndent within NoIndent should work
        assert b'[100,100,100]' in result
        assert b'[200,200,200]' in result

        # Deep nesting should not affect NoIndent
        assert b'"coordinates": [10, 20, 30, 40]' in result

    def test_unicode_handling(self):
        """Test Unicode characters in NoIndent/NoIndentNoSpace content"""
        data = {
            'unicode_noindent': NoIndent(['测试', '🚀', 'café']),
            'unicode_nospace': NoIndentNoSpace({'中文': '测试', 'emoji': '🎉'}),
            'regular_unicode': {'标题': '测试文档'}
        }

        result = json_dumps_custom_indent(data)

        # Should return bytes
        assert isinstance(result, bytes), "Function should return bytes"

        # Unicode should be preserved correctly in UTF-8 encoding
        # Note: We test by checking the UTF-8 encoded bytes
        assert '"unicode_noindent": ["测试", "🚀", "café"]'.encode('utf-8') in result
        assert '"unicode_nospace": {"中文":"测试","emoji":"🎉"}'.encode('utf-8') in result
        assert '"标题": "测试文档"'.encode('utf-8') in result

    def test_bytes_output_format(self):
        """Test that output is properly formatted UTF-8 bytes"""
        data = {
            'simple': NoIndent([1, 2, 3]),
            'nested': {
                'array': NoIndentNoSpace(['a', 'b', 'c'])
            }
        }

        result = json_dumps_custom_indent(data)

        # Should be bytes
        assert isinstance(result, bytes)

        # Should be valid UTF-8
        try:
            decoded = result.decode('utf-8')
        except UnicodeDecodeError:
            pytest.fail("Result is not valid UTF-8")

        # Should be valid JSON when decoded
        try:
            parsed = json.loads(decoded)
            assert parsed['simple'] == [1, 2, 3]
            assert parsed['nested']['array'] == ['a', 'b', 'c']
        except json.JSONDecodeError:
            pytest.fail("Result is not valid JSON")

    def test_byte_level_content_verification(self):
        """Test content at byte level to ensure exact formatting"""
        data = {
            'test': NoIndent([1, 2, 3]),
            'normal': {'nested': 'value'}
        }

        result = json_dumps_custom_indent(data)

        # Check exact byte sequences
        expected_sequences = [
            b'{\n',  # Opening brace with newline
            b'  "test": [1, 2, 3]',  # NoIndent content
            b'  "normal": {\n    "nested": "value"\n  }',  # Normal indented content
            b'\n}'  # Closing brace
        ]

        for seq in expected_sequences:
            assert seq in result, f"Expected byte sequence not found: {seq}"
