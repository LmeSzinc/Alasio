class State:
    NORMAL = 1
    SINGLE_LINE_COMMENT = 2
    MULTI_LINE_COMMENT = 3
    IN_SINGLE_QUOTE_STRING = 4
    IN_DOUBLE_QUOTE_STRING = 5  # MySQL and some other DBs use " for strings too
    IN_BACKTICK_IDENTIFIER = 6  # For MySQL `identifier`


def first_paren_content(string, balance=0):
    """
    Get content in the first parentheses
    if no paren, return ""
    if paren is unbalanced, return ""

    Args:
        string (str):
        balance (int): use balance=1 if the first "(" is already removed

    Returns:
        str:
    """
    # find first "("
    if balance == 0:
        _, sep, string = string.partition('(')
        if not sep:
            # no first "("
            return ''
        balance = 1

    end_index = -1
    for i, char in enumerate(string):
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

    return string[:end_index]


def first_token(string):
    """
    Get the first token in sql, and handle quotes

    Args:
        string (str):

    Returns:
        str:
    """
    string = string.lstrip()

    end_index = 0
    state = State.NORMAL
    for index, char in enumerate(string):
        if state == State.NORMAL:
            if char == "'":
                state = State.IN_SINGLE_QUOTE_STRING
            elif char == '"':
                state = State.IN_DOUBLE_QUOTE_STRING
            elif char == '`':
                state = State.IN_BACKTICK_IDENTIFIER
            elif char in ' \t\r\n':
                # first space
                end_index = index
                break

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

    # if no space found string[:0] will still return an empty string
    return string[:end_index]
