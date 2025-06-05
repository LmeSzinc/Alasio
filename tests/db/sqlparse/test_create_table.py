import pytest

from alasio.db.sqlparse.create_table import extract_create_table, iter_create_table


# --- Tests for extract_create_table ---

@pytest.mark.parametrize(
    "sql_input, expected_output, test_id",
    [
        (
                "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(255) NOT NULL, email VARCHAR(255) UNIQUE);",
                "id INT PRIMARY KEY, name VARCHAR(255) NOT NULL, email VARCHAR(255) UNIQUE",
                "basic_ddl",
        ),
        (
                "create table products(product_id INT, \"product Name\" VARCHAR(100), price DECIMAL(10, 2));",
                "product_id INT, \"product Name\" VARCHAR(100), price DECIMAL(10, 2)",
                "lowercase_keywords_double_quotes",
        ),
        (
                "CREATE TABLE `complex_table` (`id` int(11) NOT NULL AUTO_INCREMENT, `field,with,comma` varchar(50) "
                "DEFAULT NULL ) ENGINE=InnoDB;",
                "`id` int(11) NOT NULL AUTO_INCREMENT, `field,with,comma` varchar(50) DEFAULT NULL",
                "mysql_backticks_engine",
        ),
        (
                "CREATE TABLE empty_table ()",
                "",
                "empty_definitions_no_space",
        ),
        (
                "CREATE TABLE another_empty_table(   );",
                "",
                "empty_definitions_with_space",
        ),
        (
                "CREATE TABLE T (C1 INT NOT NULL, C2 VARCHAR(10) DEFAULT 'foo''bar', C3 TEXT CHECK (C3 != 'forbidden,value'));",
                "C1 INT NOT NULL, C2 VARCHAR(10) DEFAULT 'foo''bar', C3 TEXT CHECK (C3 != 'forbidden,value')",
                "escaped_quotes_and_check_constraint",
        ),
        (
                "CREATE TABLE with_nested_parens( id int, data_config JSON DEFAULT ('{\"options\": [1,2,3], "
                "\"enabled\": true}'), status VARCHAR(20));",
                "id int, data_config JSON DEFAULT ('{\"options\": [1,2,3], \"enabled\": true}'), status VARCHAR(20)",
                "nested_parentheses_in_default",
        ),
        (
                "SELECT * FROM users;",
                "",
                "not_create_table_select",
        ),
        (
                "CREATE TABLE malformed (id INT",  # Missing closing parenthesis for the table definition
                "",
                "malformed_missing_closing_paren",
        ),
        (
                "CREATE TABLE NO_PAREN",
                "",
                "no_parentheses_after_table",
        ),
        (
                "CREATE    TABLE    LOTS_OF_SPACES   (   field1   TYPE   )   ",
                "field1   TYPE",  # Inner spacing is preserved, outer is stripped by .strip()
                "lots_of_spaces_around_keywords_and_content",
        ),
        (
                "  leading space CREATE TABLE (content)",
                "content",
                "leading_space_before_create",
        ),
        (
                "CREATE TABLE only_create_table",  # No opening paren
                "",
                "no_opening_paren"
        ),
        (
                "CREATE TABLE `foo` (`a` int, `b` int) PARTITION BY LIST (`a`) (PARTITION `p0` VALUES IN (1,2,3));",
                "`a` int, `b` int",  # Content before PARTITION BY
                "partition_by_clause"
        ),
        (
                "CREATE TABLE table_with_comment_inside_def (col1 INT /* comment */, col2 TEXT);",
                "col1 INT /* comment */, col2 TEXT",  # Comment inside definition block is extracted
                "comment_inside_definition"
        ),
        (
                "CREATE TABLE table_with_comment_after_def (col1 INT, col2 TEXT) /* comment after table */;",
                "col1 INT, col2 TEXT",
                "comment_after_definition_block"
        ),
        (
                "CREATE /* pre-table comment */ TABLE /* post-table comment */ my_table /* pre-paren comment */ ( /* intra-paren comment */ col1 INT )",
                "/* intra-paren comment */ col1 INT",
                # extract_create_table is simple, may pick up comments if they don't break keyword detection
                "comments_everywhere_extract_check"
        )
    ],
)
def test_extract_create_table(sql_input, expected_output, test_id):
    # The current extract_create_table doesn't call remove_comment.
    # So, if comments are inside the parentheses, they will be part of the output.
    assert extract_create_table(sql_input) == expected_output


# --- Tests for iter_create_table ---

@pytest.mark.parametrize(
    "definitions_block, expected_definitions, test_id",
    [
        (
                "id INT PRIMARY KEY, name VARCHAR(255) NOT NULL, email VARCHAR(255) UNIQUE",
                ["id INT PRIMARY KEY", "name VARCHAR(255) NOT NULL", "email VARCHAR(255) UNIQUE"],
                "basic_3_columns",
        ),
        (
                "product_id INT, \"product Name\" VARCHAR(100), price DECIMAL(10, 2)",
                ["product_id INT", "\"product Name\" VARCHAR(100)", "price DECIMAL(10, 2)"],
                "double_quoted_identifier",
        ),
        (
                "`id` int(11) NOT NULL AUTO_INCREMENT, `field,with,comma` varchar(50) DEFAULT NULL",
                ["`id` int(11) NOT NULL AUTO_INCREMENT", "`field,with,comma` varchar(50) DEFAULT NULL"],
                "backticked_identifier_with_comma",
        ),
        (
                "",  # Empty definitions block
                [],
                "empty_block",
        ),
        (
                "   ",  # Whitespace only definitions block
                [],
                "whitespace_only_block",
        ),
        (
                "C1 INT NOT NULL, C2 VARCHAR(10) DEFAULT 'foo''bar', C3 TEXT CHECK (C3 != 'forbidden,value')",
                ["C1 INT NOT NULL", "C2 VARCHAR(10) DEFAULT 'foo''bar'", "C3 TEXT CHECK (C3 != 'forbidden,value')"],
                "string_literal_with_escaped_quote_and_check_constraint_with_comma",
        ),
        (
                "id int, data_config JSON DEFAULT ('{\"options\": [1,2,3], \"enabled\": true}'), status VARCHAR(20)",
                ["id int", "data_config JSON DEFAULT ('{\"options\": [1,2,3], \"enabled\": true}')",
                 "status VARCHAR(20)"],
                "json_default_with_commas_and_parentheses",
        ),
        (
                "col1 INT",
                ["col1 INT"],
                "single_column",
        ),
        (
                "col1 INT,    col2 TEXT   , col3 VARCHAR(10) DEFAULT 'val,ue'",
                ["col1 INT", "col2 TEXT", "col3 VARCHAR(10) DEFAULT 'val,ue'"],
                "spacing_around_commas_and_in_string",
        ),
        (
                "col1 INT ,, col2 TEXT",  # Double comma
                ["col1 INT", "col2 TEXT"],
                "double_comma_separator",
        ),
        (
                ",col1 INT, col2 TEXT,",  # Leading and trailing commas
                ["col1 INT", "col2 TEXT"],
                "leading_and_trailing_commas",
        ),
        (
                "CONSTRAINT pk_product PRIMARY KEY (product_id, \"product Name\")",
                ["CONSTRAINT pk_product PRIMARY KEY (product_id, \"product Name\")"],
                "constraint_definition_with_comma_in_parentheses",
        ),
        (
                "field1 TYPE, field2 TEXT DEFAULT 'value with (paren)', field3 `ident with (paren)`",
                ["field1 TYPE", "field2 TEXT DEFAULT 'value with (paren)'", "field3 `ident with (paren)`"],
                "parentheses_inside_quotes_and_backticks"
        ),
        (
                # Test for comments within the definition block passed to iter_create_table
                "col1 INT -- this is a comment\n, col2 TEXT /* another comment */, col3 VARCHAR",
                ["col1 INT -- this is a comment", "col2 TEXT /* another comment */", "col3 VARCHAR"],
                "comments_within_definitions"  # iter_create_table doesn't remove them, treats as part of def
        ),
        (
                "col1 INT CHECK (col1 > 0 AND col1 < 100), col2 TEXT",
                ["col1 INT CHECK (col1 > 0 AND col1 < 100)", "col2 TEXT"],
                "check_constraint_no_internal_comma"
        ),
        (
                "col1 TEXT DEFAULT 'hello world'",  # No comma
                ["col1 TEXT DEFAULT 'hello world'"],
                "single_definition_no_comma_at_all"
        ),
        (
                "value TEXT DEFAULT 'It''s a string, with a comma and ''escaped'' quotes.'",
                ["value TEXT DEFAULT 'It''s a string, with a comma and ''escaped'' quotes.'"],
                "string_with_escaped_quotes_and_comma"
                # iter_create_table does not handle '' as escaped, but as two quotes.
        )

    ],
)
def test_iter_create_table(definitions_block, expected_definitions, test_id):
    # iter_create_table is a generator, so convert its output to a list for comparison
    # It also does not implement its own comment removal.
    # If comments are part of its input string, they will be part of the yielded definitions.
    assert list(iter_create_table(definitions_block)) == expected_definitions
