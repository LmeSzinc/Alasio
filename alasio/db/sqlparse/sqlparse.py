from collections import deque


class State:
    NORMAL = 1
    SINGLE_LINE_COMMENT = 2
    MULTI_LINE_COMMENT = 3
    IN_SINGLE_QUOTE_STRING = 4
    IN_DOUBLE_QUOTE_STRING = 5  # MySQL and some other DBs use " for strings too
    IN_BACKTICK_IDENTIFIER = 6  # For MySQL `identifier`


def remove_comment(sql):
    """
    Removes single-line (--, #) and multi-line (/* ... */) comments from a SQL query string,
    with awareness for MySQL-specifics like '#' comments and backtick-quoted identifiers.
    Handles comments correctly even if they appear inside string literals or quoted identifiers.

    Args:
        sql (str): The SQL query string.

    Returns:
        str: The SQL query string with comments removed.
    """
    if not sql:
        return sql
    # check comment keyword
    if '/*' not in sql and '--' not in sql and '#' not in sql:
        return sql

    result = deque()
    current_state = State.NORMAL
    i = 0
    n = len(sql)

    while i < n:
        char = sql[i]
        # next_char = sql_query[i + 1] if i + 1 < n else None

        if current_state == State.NORMAL:
            if char == '-':
                try:
                    if sql[i + 1] == '-':
                        current_state = State.SINGLE_LINE_COMMENT
                        i += 2  # Consume '--'
                        continue
                except IndexError:
                    pass
            elif char == '#':  # MySQL single-line comment
                current_state = State.SINGLE_LINE_COMMENT
                i += 1  # Consume '#'
                continue
            elif char == '/':
                try:
                    if sql[i + 1] == '*':
                        current_state = State.MULTI_LINE_COMMENT
                        i += 2  # Consume '/*'
                        continue
                except IndexError:
                    pass
            elif char == "'":
                current_state = State.IN_SINGLE_QUOTE_STRING
            elif char == '"':
                current_state = State.IN_DOUBLE_QUOTE_STRING
            elif char == '`':  # MySQL backtick-quoted identifier
                current_state = State.IN_BACKTICK_IDENTIFIER
            # Add normal character
            result.append(char)

        elif current_state == State.SINGLE_LINE_COMMENT:
            if char == '\n':
                current_state = State.NORMAL
                result.append(char)  # Preserve newline
            # Else, do nothing (character is part of the comment)

        elif current_state == State.MULTI_LINE_COMMENT:
            try:
                if char == '*' and sql[i + 1] == '/':
                    current_state = State.NORMAL
                    i += 2  # Consume '*/'
                    continue
            except IndexError:
                pass
            # Else, do nothing (character is part of the comment)

        elif current_state == State.IN_SINGLE_QUOTE_STRING:
            result.append(char)
            if char == "'":
                # Handle escaped single quotes like 'O''Reilly'
                try:
                    next_char = sql[i + 1]
                except IndexError:
                    next_char = ''
                if next_char == "'":
                    result.append(next_char)
                    i += 1  # Consume the escaped quote
                else:
                    current_state = State.NORMAL

        elif current_state == State.IN_DOUBLE_QUOTE_STRING:
            result.append(char)
            if char == '"':
                # Handle escaped double quotes "ident""ifier" (SQL standard)
                # or "string\"" (some dialects, less common in SQL for strings)
                try:
                    next_char = sql[i + 1]
                except IndexError:
                    next_char = ''
                if next_char == '"':
                    result.append(next_char)
                    i += 1
                else:
                    current_state = State.NORMAL

        elif current_state == State.IN_BACKTICK_IDENTIFIER:
            result.append(char)
            if char == '`':
                # Handle escaped backticks like `ident``ifier`
                try:
                    next_char = sql[i + 1]
                except IndexError:
                    next_char = ''
                if next_char == '`':
                    result.append(next_char)
                    i += 1  # Consume the escaped backtick
                else:
                    current_state = State.NORMAL

        i += 1

    return "".join(result)
