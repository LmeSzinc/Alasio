def convert_number(num_str):
    """
    Convert a string to either int or float based on its format.

    Args:
        num_str (str): String representing a number

    Returns:
        int | float: Converted number maintaining the original type
    """
    if '.' in num_str:
        return float(num_str)
    else:
        return int(num_str)


def parse_single_comparison(comp_str):
    """
    Parse a single comparison expression (e.g., 'x>5', '<=10.5').

    Args:
        comp_str (str): String containing a comparison operator and a number

    Returns:
        tuple: (operator_key, value) or (None, None) if parsing fails
    """
    comp_str = comp_str.replace('x', '')
    if comp_str.startswith("<="):
        return 'le', convert_number(comp_str[2:])
    elif comp_str.startswith("<"):
        return 'lt', convert_number(comp_str[1:])
    elif comp_str.startswith(">="):
        return 'ge', convert_number(comp_str[2:])
    elif comp_str.startswith(">"):
        return 'gt', convert_number(comp_str[1:])
    else:
        raise ValueError(comp_str)


def parse_range(range_str):
    """
    Parse different range expression formats into a dictionary with comparison operators.
    Preserves the type of input numbers (int or float).

    Args:
        range_str (str):
            - "a~b" (a to b inclusive)
            - "a<=x<=b" (a less than or equal to x less than or equal to b)
            - ">a,<b" (greater than a, less than b)

    Returns:
        dict:
            - 'le': less than or equal to value (if applicable)
            - 'lt': less than value (if applicable)
            - 'ge': greater than or equal to value (if applicable)
            - 'gt': greater than value (if applicable)
    """
    range_str = str(range_str).strip().replace(' ', '').lower()
    result = {}

    # Handle a~b format
    for sep in '~～˜-—':
        if sep in range_str:
            left, _, right = range_str.partition("~")
            result['ge'] = convert_number(left)
            result['le'] = convert_number(right)
            return result
    # Handle comparison operators with comma
    if ',' in range_str:
        comparisons = range_str.split(',')
        for comp in comparisons:
            key, value = parse_single_comparison(comp)
            if key is not None:
                result[key] = value
        return result
    # Handle a<=x<=b format
    if 'x' in range_str:
        comparisons = range_str.split('x')
        for comp in comparisons:
            key, value = parse_single_comparison(comp)
            if key is not None:
                result[key] = value
        return result
    # Handle single comparison operators
    key, value = parse_single_comparison(range_str)
    if key is not None:
        result[key] = value
    return result
