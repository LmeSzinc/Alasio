import pytest

from alasio.db.sqlparse.comment import remove_comment

sql_complex_mysql_input = """
SELECT
    `c`.`id` AS `customer_id`, # Customer's unique ID
    `c`.`name` AS `customer_name`, -- Customer's full name
    `o`.`order_date`,
    `oi`.`product_name`
FROM
    `customers` `c` /* Customer table alias */
JOIN
    `orders` `o` ON `c`.`id` = `o`.`customer_id`
    -- Join condition for orders
JOIN
    `order_items` `oi` ON `o`.`id` = `oi`.`order_id`
WHERE
    `c`.`registration_date` > '2023-01-01' # Only new customers
    AND `oi`.`price` > 10.00 -- Expensive items
    AND `c`.`status` = 'active'
    AND `c`.`notes` LIKE '%important `note`%' -- Note: backtick inside string literal
    # Final filter on order status
    AND `o`.`status` <> 'cancelled';
"""

# Note: The expected output requires careful handling of whitespace and newlines.
# Single line comments remove content up to (but not including) the newline.
# Multi-line comments are removed entirely. Spaces around them are preserved if they are outside the comment.
sql_complex_mysql_expected = """
SELECT
    `c`.`id` AS `customer_id`, 
    `c`.`name` AS `customer_name`, 
    `o`.`order_date`,
    `oi`.`product_name`
FROM
    `customers` `c` 
JOIN
    `orders` `o` ON `c`.`id` = `o`.`customer_id`
    
JOIN
    `order_items` `oi` ON `o`.`id` = `oi`.`order_id`
WHERE
    `c`.`registration_date` > '2023-01-01' 
    AND `oi`.`price` > 10.00 
    AND `c`.`status` = 'active'
    AND `c`.`notes` LIKE '%important `note`%' 
    
    AND `o`.`status` <> 'cancelled';
"""

TEST_CASES = [
    ("empty_string", "", ""),
    ("no_comments", "SELECT * FROM test;", "SELECT * FROM test;"),
    ("no_comments_quick_check_bypass", "SELECT 1", "SELECT 1"),  # Test the 'if not in' bypass
    ("simple_dash_comment", "SELECT 1; -- comment", "SELECT 1; "),
    ("simple_dash_comment_with_newline", "SELECT 1; -- comment\nEND", "SELECT 1; \nEND"),
    ("simple_hash_comment", "SELECT 1; # comment", "SELECT 1; "),
    ("simple_hash_comment_with_newline", "SELECT 1; # comment\nEND", "SELECT 1; \nEND"),
    ("simple_multiline_comment", "SELECT /* comment */ 1;", "SELECT  1;"),
    ("multiline_spanning_lines", "SELECT 1\n/* multi\nline */\nFROM test;", "SELECT 1\n\nFROM test;"),
    ("comment_in_single_quotes", "SELECT 'text -- with comment # and /* */' AS val;",
     "SELECT 'text -- with comment # and /* */' AS val;"),
    ("comment_in_double_quotes", "SELECT \"text -- with comment # and /* */\" AS val;",
     "SELECT \"text -- with comment # and /* */\" AS val;"),
    ("comment_in_backticks", "SELECT `ident--ifier#name/*foo*/` AS val;", "SELECT `ident--ifier#name/*foo*/` AS val;"),
    ("escaped_single_quotes", "SELECT 'O''Reilly';", "SELECT 'O''Reilly';"),
    ("escaped_double_quotes", "SELECT \"ident\"\"ifier\";", "SELECT \"ident\"\"ifier\";"),
    ("escaped_backticks", "SELECT `ident``ifier`;", "SELECT `ident``ifier`;"),
    ("unterminated_multiline", "SELECT 1 /* unterminated", "SELECT 1 "),
    ("unterminated_multiline_at_eof", "SELECT 1 /*", "SELECT 1 "),
    ("unterminated_dash_comment", "SELECT 1 -- unterminated", "SELECT 1 "),  # Consumes till end
    ("unterminated_hash_comment", "SELECT 1 # unterminated", "SELECT 1 "),  # Consumes till end
    ("comment_at_start_dash", "-- start comment\nSELECT 1;", "\nSELECT 1;"),
    ("comment_at_start_hash", "# start comment\nSELECT 1;", "\nSELECT 1;"),
    ("comment_at_start_multiline", "/* start comment */SELECT 1;", "SELECT 1;"),
    ("comment_at_end_no_newline_dash", "SELECT 1; --end", "SELECT 1; "),
    ("comment_at_end_no_newline_hash", "SELECT 1; #end", "SELECT 1; "),
    ("comment_at_end_with_newline_dash", "SELECT 1; --end\n", "SELECT 1; \n"),
    ("comment_at_end_with_newline_hash", "SELECT 1; #end\n", "SELECT 1; \n"),
    ("triple_dash", "SELECT 1; --- comment", "SELECT 1; "),  # Should start comment at first '--'
    ("no_space_before_comment_dash", "SELECT 1;--comment", "SELECT 1;"),
    ("no_space_before_comment_hash", "SELECT 1;#comment", "SELECT 1;"),
    ("hash_at_line_start", "# HASH\nSELECT 1;", "\nSELECT 1;"),
    ("multiline_only", "/* just a comment */", ""),
    ("single_line_dash_only_no_newline", "-- just a comment", ""),
    ("single_line_hash_only_no_newline", "# just a comment", ""),
    ("single_line_dash_only_with_newline", "-- just a comment\n", "\n"),
    ("single_line_hash_only_with_newline", "# just a comment\n", "\n"),
    ("incomplete_marker_slash", "SELECT 1 / not a comment", "SELECT 1 / not a comment"),
    ("incomplete_marker_dash", "SELECT 1 - not a comment", "SELECT 1 - not a comment"),
    ("incomplete_marker_dash_at_end", "SELECT 1 -", "SELECT 1 -"),
    ("incomplete_marker_slash_at_end", "SELECT 1 /", "SELECT 1 /"),
    ("mysql_example_0", "SELECT * FROM users; -- This is a comment", "SELECT * FROM users; "),
    ("mysql_example_1", "SELECT name, /* This is a\nmulti-line comment */ email FROM customers;",
     "SELECT name,  email FROM customers;"),
    ("mysql_example_2", "SELECT * FROM orders # This is a MySQL comment", "SELECT * FROM orders "),
    ("mysql_example_8", "SELECT `escaped``backtick` FROM `test`; # MySQL escaped backtick",
     "SELECT `escaped``backtick` FROM `test`; "),
    ("mysql_example_10", "SELECT 1; #comment at end", "SELECT 1; "),
    ("mysql_example_11",
     "/* block comment */ SELECT `field` # line comment\nFROM `table` -- another line comment\nWHERE `id` = 1;",
     " SELECT `field` \nFROM `table` \nWHERE `id` = 1;"),
    ("mysql_example_12", "SELECT `field` FROM `table` #\nWHERE `condition`; # Empty hash comment",
     "SELECT `field` FROM `table` \nWHERE `condition`; "),
    ("complex_mysql_example", sql_complex_mysql_input, sql_complex_mysql_expected),
    ("comment_like_sequences_in_strings", "SELECT '--not a comment', \"#not a comment\", `/*not a comment*/`",
     "SELECT '--not a comment', \"#not a comment\", `/*not a comment*/`"),
    ("string_ending_with_comment_char_single", "SELECT 'test-'--comment", "SELECT 'test-'"),
    # String ends, then comment starts
    ("string_ending_with_comment_char_double", "SELECT \"test-\"--comment", "SELECT \"test-\""),
    ("string_ending_with_comment_char_backtick", "SELECT `test-`--comment", "SELECT `test-`"),
    ("string_ending_with_comment_char_slash", "SELECT 'test/'/*comment*/", "SELECT 'test/'"),
    ("dash_before_string_not_comment", "SELECT -'val';", "SELECT -'val';"),  # `-` is not `--`
    ("comment_after_escaped_quote_in_string", "SELECT 'test''--still in string' -- comment",
     "SELECT 'test''--still in string' "),
]


@pytest.mark.parametrize("name, sql_input, expected_output", TEST_CASES, ids=[tc[0] for tc in TEST_CASES])
def test_remove_sql_comment(name, sql_input, expected_output):
    """
    Tests the remove_sql_comment function with various scenarios.
    """
    # print(f"\nTesting: {name}")
    # print(f"Input:    '{sql_input}'")
    # print(f"Expected: '{expected_output}'")
    actual_output = remove_comment(sql_input)
    # print(f"Actual:   '{actual_output}'")
    assert actual_output == expected_output, f"Test case '{name}' failed."
