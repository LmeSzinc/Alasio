from alasio.db.sqlparse.comment import State


def extract_create_table(sql):
    """
    Finds "CREATE", "TABLE", and the first '(' (case-insensitive for keywords,
    case-sensitive for content) using string.partition. Extracts the content
    within the first top-level parentheses that follow the "TABLE" keyword.
    The extracted content preserves its original casing from the input SQL.

    Args:
        sql (str): The SQL string.

    Returns:
        str: The content string (definitions) inside the main parentheses, stripped
            of leading/trailing whitespace. Returns an empty string "" if
            "CREATE TABLE (...) " pattern is not found or if parentheses are mismatched.
    """
    sql_upper = sql.upper()

    # This variable will track our current position in the original `sql` string
    # as we find keywords and the opening parenthesis.
    index = 0

    # Find "CREATE"
    before, sep, sql_upper = sql_upper.partition('CREATE')
    if not sep:
        return ""
    index += len(before) + 6

    # Find "TABLE"
    before, sep, sql_upper = sql_upper.partition('TABLE')
    if not sep:
        return ""
    index += len(before) + 5

    # Find "("
    before, sep, sql_upper = sql_upper.partition('(')
    if not sep:
        return ""
    index += len(before) + 1

    # We've found one opening parenthesis
    balance = 1

    # 4. Iterate from after the opening parenthesis *in the original 'sql' string*
    #    to find the matching closing parenthesis. This ensures we respect the
    #    original casing when we eventually slice the content.
    #    The balancing logic must operate on the original string to correctly handle
    #    parentheses within quoted identifiers or string literals if they were to be
    #    made case-sensitive (though standard SQL quotes are not).
    #    For simple parenthesis balancing, using the original string is robust.
    sql = sql[index:]
    end_index = -1
    for i, char in enumerate(sql):
        if char == '(':
            balance += 1
        elif char == ')':
            balance -= 1
            if balance == 0:
                # Matching closing parenthesis found
                end_index = i
                break

    if end_index == -1:
        # No matching closing parenthesis found for the block
        return ''

    # Return the content, preserving original casing, and stripped of outer whitespace.
    return sql[:end_index].strip()


def iter_create_table(sql):
    """
    Takes the extracted definitions block from a CREATE TABLE statement
    and yields each column definition or top-level constraint using slicing.
    Handles commas within parentheses () and within quoted strings/identifiers
    (single ', double ", backtick `).
    This version does NOT implement special handling for escaped quotes
    (e.g., '' inside a string is treated as two separate quotes toggling state).

    Args:
        sql (str): The string content from within the main
           parentheses of a CREATE TABLE statement.

    Yields:
        str: Each definition line (a slice of the original definitions_block,
             stripped of leading/trailing whitespace).
    """
    # Handles None or empty string (e.g., from "CREATE TABLE foo()")
    if not sql:
        return

    row_start = 0
    nested = 0
    state = State.NORMAL

    for index, char in enumerate(sql):
        if state == State.NORMAL:
            if char == "'":
                state = State.IN_SINGLE_QUOTE_STRING
            elif char == '"':
                state = State.IN_DOUBLE_QUOTE_STRING
            elif char == '`':
                state = State.IN_BACKTICK_IDENTIFIER
            elif char == '(':
                nested += 1
            elif char == ')':
                # Ensure nested level doesn't go below zero if parentheses are mismatched within segment
                nested -= 1
                if nested < 0:
                    nested = 0
            elif char == ',' and nested == 0:
                # Found a top-level comma, signaling the end of a definition
                # Slice from the original definitions_block
                definition = sql[row_start:index].strip()
                # Avoid yielding empty strings (e.g., from ",," or leading/trailing comma)
                if definition:
                    yield definition
                # Next definition starts after this comma
                row_start = index + 1

        elif state == State.IN_SINGLE_QUOTE_STRING:
            # Single quote ends the string state
            if char == "'":
                state = State.NORMAL

        elif state == State.IN_DOUBLE_QUOTE_STRING:
            # Double quote ends the string/identifier state
            if char == '"':
                state = State.NORMAL

        elif state == State.IN_BACKTICK_IDENTIFIER:
            # Backtick ends the identifier state
            if char == '`':
                state = State.NORMAL

    # After the loop, yield the last remaining segment (or the only segment if no top-level commas)
    last_definition = sql[row_start:].strip()
    # Avoid yielding empty string if trailing content was just whitespace
    if last_definition:
        yield last_definition
