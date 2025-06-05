import pytest

from alasio.db.sqlparse.column import get_primary_key

# Test cases: (input_sql_line, expected_output_columns_string)
# Assumption: Each input_sql_line is a syntactically correct line
# from within a CREATE TABLE statement. Comments are pre-stripped.
PRIMARY_KEY_TEST_CASES = [
    # --- Inline PK Definitions (Valid column definition line with PK) ---
    ("id INT PRIMARY KEY", "id"),
    ("  `user_id` BIGINT NOT NULL PRIMARY KEY,", "`user_id`"),  # Trailing comma is fine
    ('[Order Number] NVARCHAR(50) PRIMARY KEY UNIQUE,', "[Order Number]"),
    ('"CamelCaseName" TEXT PRIMARY KEY', '"CamelCaseName"'),
    ("column1 VARCHAR(255) PRIMARY KEY NOT NULL", "column1"),
    ("col_with_underscore_type TEXT PRIMARY KEY", "col_with_underscore_type"),
    ("id int primary key", "id"),  # Case-insensitivity for keywords
    ("complex_id VARCHAR(30) NOT NULL PRIMARY KEY CHECK (complex_id <> ''),", "complex_id"),
    ("id INT PRIMARY KEY USING BTREE", "id"),  # PostgreSQL specific syntax for inline

    # --- Separate PK Constraint Definitions (Valid table-level constraint line) ---
    ("PRIMARY KEY (id)", "id"),
    ("  PRIMARY KEY  (  `col1` , \"col2\"   )  ", "`col1` , \"col2\""),  # Spaces around columns
    ("CONSTRAINT pk_my_table PRIMARY KEY (order_id, item_id)", "order_id, item_id"),
    ("CONSTRAINT \"PK_Users\" PRIMARY KEY (\"UserID\")", "\"UserID\""),
    ("CONSTRAINT [PK_Orders] PRIMARY KEY ([OrderID], [ProductID])", "[OrderID], [ProductID]"),
    ("CONSTRAINT PRIMARY KEY (legacy_id)", "legacy_id"),  # Oracle style unnamed constraint
    ("primary key (id)", "id"),  # Case-insensitivity
    ("CONSTRAINT pk1 PRIMARY KEY(col1,col2)", "col1,col2"),  # No space after KEY
    ("PRIMARY KEY NONCLUSTERED (ProductID)", "ProductID"),  # SQL Server specific keyword
    ("PRIMARY KEY CLUSTERED (EventID) WITH (IGNORE_DUP_KEY = ON)", "EventID"),  # More SQL Server
    ("PRIMARY KEY (col_a (5) ASC)", "col_a (5) ASC"),  # MySQL with length/sort
    ("PRIMARY KEY (col_a, (col_b || col_c))", "col_a, (col_b || col_c)"),  # Expression in PK (SQLite)
    ("PRIMARY KEY ((expr_col))", "(expr_col)"),  # Expression with extra parentheses
    ("PRIMARY   KEY   (col1)", "col1"),  # Multiple spaces between PRIMARY and KEY
    ("PRIMARY KEY(`col1`)", "`col1`"),  # No space before parenthesis of column list

    # --- Negative Cases: Valid SQL lines that are NOT PK definitions ---
    # Standard column definitions
    ("name VARCHAR(100) NOT NULL,", ""),
    ("age INT DEFAULT 0,", ""),
    ("`timestamp` timestamp with time zone DEFAULT now(),", ""),
    # Other constraint types
    ("UNIQUE (email),", ""),
    ("CONSTRAINT uk_email UNIQUE (email),", ""),
    ("FOREIGN KEY (user_id) REFERENCES users(id),", ""),
    ("CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id),", ""),
    ("CHECK (price > 0),", ""),
    ("CONSTRAINT chk_price CHECK (price > 0),", ""),
    # Index definitions (some DBs allow in CREATE TABLE)
    ("INDEX idx_name (name),", ""),
    ("KEY idx_email (email),", ""),  # MySQL alias for INDEX
    # Table options / misc
    ("LIKE other_table INCLUDING ALL,", ""),
    # Column named similarly to keywords but not a PK definition itself
    ("PRIMARY_KEY_COLUMN INT NOT NULL,", ""),
    ("`primary key` varchar(10) NOT NULL,", ""),  # Quoted column name
    # Incomplete definitions (if this is the *entire* line, it's not a full PK def)
    ("PRIMARY KEY NONCLUSTERED", ""),  # Missing column list for separate PK
    ("PRIMARY KEY CLUSTERED", ""),  # Missing column list for separate PK
    ("CONSTRAINT my_pk", ""),  # Incomplete constraint definition
    ("primary key", ""),  # Keywords present, but no column definition
    # Lines where "PRIMARY KEY" appears but not as the primary definition of *this* line
    ("id INT, CONSTRAINT pk_id PRIMARY KEY(id)", ""),  # Same as above
    # Malformed (but still testing parser's ability to reject non-PKs)
    ("id PRIMARY KEY", ""),  # Missing type for inline PK (invalid SQL for column, but parser should reject)
    ("id PRIMARY KEY NOT NULL", ""),  # Missing type
    ("primary key `col1` int", ""),  # Malformed sequence
    # Not "PRIMARY KEY"
    ("KEY (col1)", ""),  # MySQL index, not PK
    ("PRIMARY (col1)", ""),  # Incomplete "PRIMARY KEY"
    # Other valid lines within CREATE TABLE
    ("CREATE TABLE my_table (", ""),  # Start of CREATE TABLE, not a PK line
    ("", ""),  # Empty string input
    ("    ", ""),  # String with only spaces
    # Ensure whole word matching for "PRIMARY KEY"
    ("ID INT NOTPRIMARY KEY (ID)", ""),
    ("ID INT MYPRIMARY KEY (ID)", ""),
    ("ID INT PRIMARY_KEY_FUNC(ID)", ""),
    # First token is a keyword (should not be treated as column name for inline PK)
    ("PRIMARY INT KEY", ""),  # If "PRIMARY" was col name, "INT" type, "KEY" (not "PRIMARY KEY")
    ("TABLE INT PRIMARY KEY", ""),  # `TABLE` as col name
]


@pytest.mark.parametrize("sql_line, expected_output", PRIMARY_KEY_TEST_CASES)
def test_get_primary_key(sql_line, expected_output):
    """
    Tests the get_primary_key function with various syntactically correct SQL lines
    from within a CREATE TABLE statement.
    """
    assert get_primary_key(sql_line) == expected_output
