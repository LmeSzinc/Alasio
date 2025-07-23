from alasio.config_dev.format.format_yaml import yaml_formatter


class TestYamlFormatter:
    """Test cases for YAML formatter function"""

    def test_empty_input(self):
        """Test empty input returns single newline"""
        assert yaml_formatter(b"") == b""
        assert yaml_formatter(b"   ") == b""
        assert yaml_formatter(b"\n\n") == b""

    def test_single_root_object(self):
        """Test single root-level object ends with blank line"""
        input_yaml = b"key: value"
        expected = b"key: value\n"
        assert yaml_formatter(input_yaml) == expected

    def test_multiple_root_objects_spacing(self):
        """Test root-level objects are separated by blank lines"""
        input_yaml = b"""key1: value1
key2: value2
key3: value3"""
        expected = b"""key1: value1

key2: value2

key3: value3
"""
        assert yaml_formatter(input_yaml) == expected

    def test_nested_content_no_spacing(self):
        """Test nested content has no blank lines between items"""
        input_yaml = b"""key1: value1
  nested1: value1_1

  nested2: value1_2


key2: value2
  nested1: value2_1

  nested2: value2_2"""
        expected = b"""key1: value1
  nested1: value1_1
  nested2: value1_2

key2: value2
  nested1: value2_1
  nested2: value2_2
"""
        assert yaml_formatter(input_yaml) == expected

    def test_standalone_comments(self):
        """Test standalone comments (followed by blank line) get separate blocks"""
        input_yaml = b"""# Standalone comment 1
# Standalone comment 2

key1: value1

# Another standalone comment

key2: value2"""
        expected = b"""# Standalone comment 1
# Standalone comment 2

key1: value1

# Another standalone comment

key2: value2
"""
        assert yaml_formatter(input_yaml) == expected

    def test_grouped_comments(self):
        """Test grouped comments (directly followed by object) stay together"""
        input_yaml = b"""# Comment attached to key1
key1: value1

# Multi-line comment
# attached to key2
key2: value2"""
        expected = b"""# Comment attached to key1
key1: value1

# Multi-line comment
# attached to key2
key2: value2
"""
        assert yaml_formatter(input_yaml) == expected

    def test_mixed_comments_and_objects(self):
        """Test complex mix of standalone and grouped comments"""
        input_yaml = b"""# Standalone comment

key1: value1
  nested: value

# Grouped comment
key2: value2

# Another standalone

# Grouped with key3
key3: value3"""
        expected = b"""# Standalone comment

key1: value1
  nested: value

# Grouped comment
key2: value2

# Another standalone

# Grouped with key3
key3: value3
"""
        assert yaml_formatter(input_yaml) == expected

    def test_trailing_spaces_removed(self):
        """Test trailing spaces are removed from all lines"""
        input_yaml = b"""key1: value1   
  nested: value   

key2: value2   """
        expected = b"""key1: value1
  nested: value

key2: value2
"""
        assert yaml_formatter(input_yaml) == expected

    def test_yaml_lists(self):
        """Test YAML list formatting"""
        input_yaml = b"""items:
  - item1

  - item2
  - item3

other_key: value"""
        expected = b"""items:
  - item1
  - item2
  - item3

other_key: value
"""
        assert yaml_formatter(input_yaml) == expected

    def test_nested_objects(self):
        """Test deeply nested objects"""
        input_yaml = b"""root:
  level1:
    level2:
      key: value

    another: value

  back_to_level1: value

another_root: value"""
        expected = b"""root:
  level1:
    level2:
      key: value
    another: value
  back_to_level1: value

another_root: value
"""
        assert yaml_formatter(input_yaml) == expected

    def test_comments_only(self):
        """Test file with only comments"""
        input_yaml = b"""# Comment 1
# Comment 2

# Standalone comment"""
        expected = b"""# Comment 1
# Comment 2

# Standalone comment
"""
        assert yaml_formatter(input_yaml) == expected

    def test_excessive_blank_lines(self):
        """Test removal of excessive blank lines"""
        input_yaml = b"""key1: value1



key2: value2


  nested: value



key3: value3"""
        expected = b"""key1: value1

key2: value2
  nested: value

key3: value3
"""
        assert yaml_formatter(input_yaml) == expected

    def test_comment_at_file_end(self):
        """Test standalone comment at end of file"""
        input_yaml = b"""key1: value1

# Final comment"""
        expected = b"""key1: value1

# Final comment
"""
        assert yaml_formatter(input_yaml) == expected

    def test_grouped_comment_at_file_end(self):
        """Test grouped comment at end of file"""
        input_yaml = b"""key1: value1

# Comment for key2
key2: value2"""
        expected = b"""key1: value1

# Comment for key2
key2: value2
"""
        assert yaml_formatter(input_yaml) == expected

    def test_indented_comments_ignored(self):
        """Test indented comments are treated as nested content"""
        input_yaml = b"""key1: value1
  # This is nested comment
  nested_key: value

key2: value2"""
        expected = b"""key1: value1
  # This is nested comment
  nested_key: value

key2: value2
"""
        assert yaml_formatter(input_yaml) == expected

    def test_tabs_vs_spaces(self):
        """Test both tabs and spaces are recognized as indentation"""
        input_yaml = b"""key1: value1
\tnested_with_tab: value
  nested_with_spaces: value

key2: value2"""
        expected = b"""key1: value1
\tnested_with_tab: value
  nested_with_spaces: value

key2: value2
"""
        assert yaml_formatter(input_yaml) == expected

    def test_complex_yaml_structure(self):
        """Test complex real-world-like YAML structure"""
        input_yaml = b"""# Application configuration
app:
  name: myapp
  version: 1.0.0

# Database settings

database:
  host: localhost
  port: 5432

  credentials:
    username: user
    password: pass

# Server configuration
server:
  # Port configuration
  port: 8080
  host: 0.0.0.0

  ssl:
    enabled: true

    cert_path: /path/to/cert

# Final comment"""
        expected = b"""# Application configuration
app:
  name: myapp
  version: 1.0.0

# Database settings

database:
  host: localhost
  port: 5432
  credentials:
    username: user
    password: pass

# Server configuration
server:
  # Port configuration
  port: 8080
  host: 0.0.0.0
  ssl:
    enabled: true
    cert_path: /path/to/cert

# Final comment
"""
        assert yaml_formatter(input_yaml) == expected
