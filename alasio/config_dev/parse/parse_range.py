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
    if comp_str.endswith("<="):
        return 'ge', convert_number(comp_str[:-2])
    elif comp_str.endswith("<"):
        return 'gt', convert_number(comp_str[:-1])
    else:
        raise ValueError(comp_str)


def validate_range_constraints(range_dict):
    """
    Validate that the range constraints are logically consistent.

    Args:
        range_dict (dict):
            Dictionary with range constraints containing keys:
            'gt', 'ge', 'lt', 'le' with numeric values

    Returns:
        bool: True if constraints are valid

    Raises:
        ValueError: If constraints are logically impossible
    """
    if not isinstance(range_dict, dict):
        raise ValueError("Range constraints must be a dictionary")

    if not range_dict:
        return True  # Empty dict is valid

    # Extract values, defaulting to None if not present
    gt = range_dict.get('gt')  # greater than
    ge = range_dict.get('ge')  # greater than or equal
    lt = range_dict.get('lt')  # less than
    le = range_dict.get('le')  # less than or equal

    # Check for valid keys
    valid_keys = {'gt', 'ge', 'lt', 'le'}
    invalid_keys = set(range_dict.keys()) - valid_keys
    if invalid_keys:
        raise ValueError(f"Invalid range constraint keys: {invalid_keys}")

    # Check that all values are numeric
    for key, value in range_dict.items():
        if not isinstance(value, (int, float)):
            raise ValueError(f"Range constraint '{key}' must be numeric, got {type(value).__name__}: {value}")

    # Check for redundant or conflicting same-side constraints
    # If both gt and ge are present, use the more restrictive one
    if gt is not None and ge is not None:
        if ge > gt:
            # ge is more restrictive (≥5 is stricter than >3)
            del range_dict['gt']
            gt = None
        else:
            # gt is more restrictive (>5 is stricter than ≥3)
            del range_dict['ge']
            ge = None
    # If both lt and le are present, use the more restrictive one
    if lt is not None and le is not None:
        if le < lt:
            # le is more restrictive (≤5 is stricter than <10)
            del range_dict['lt']
            lt = None
        else:
            # lt is more restrictive (<5 is stricter than ≤10)
            del range_dict['le']
            le = None

    # Check for logically impossible constraints

    # Case 1: gt vs lt/le constraints
    if gt is not None:
        if lt is not None and gt >= lt:
            raise ValueError(f"Impossible constraint: must be greater than {gt} AND less than {lt}")
        if le is not None and gt >= le:
            raise ValueError(f"Impossible constraint: must be greater than {gt} AND less than or equal to {le}")

    # Case 2: ge vs lt/le constraints
    if ge is not None:
        if lt is not None and ge >= lt:
            raise ValueError(f"Impossible constraint: must be greater than or equal to {ge} AND less than {lt}")
        if le is not None and ge > le:
            raise ValueError(
                f"Impossible constraint: must be greater than or equal to {ge} AND less than or equal to {le}")

    return True


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
    if len(range_str) > 140:
        raise ValueError(f'range_str too long: {range_str[:100]}')
    if '，' in range_str:
        range_str.replace('，', ',')

    result = {}

    # Handle a~b format
    for sep in '~～˜':
        if sep in range_str:
            left, _, right = range_str.partition(sep)
            result['ge'] = convert_number(left)
            result['le'] = convert_number(right)
            validate_range_constraints(result)
            return result

    # Handle comparison operators with comma
    if ',' in range_str:
        comparisons = range_str.split(',')
        for comp in comparisons:
            key, value = parse_single_comparison(comp)
            result[key] = value
        validate_range_constraints(result)
        return result

    # Handle a<=x<=b format
    if 'x' in range_str:
        comparisons = range_str.split('x')
        for comp in comparisons:
            if not comp:
                continue
            key, value = parse_single_comparison(comp)
            result[key] = value
        validate_range_constraints(result)
        return result

    # Handle a-b format
    for sep in '-—':
        if sep not in range_str:
            continue
        # Handle <-5 before a-b
        for char in ['>', '=', '<']:
            if char not in range_str:
                continue
            try:
                key, value = parse_single_comparison(range_str)
                result[key] = value
            except ValueError:
                break
            else:
                validate_range_constraints(result)
                return result
        left, _, right = range_str.partition(sep)
        result['ge'] = convert_number(left)
        result['le'] = convert_number(right)
        validate_range_constraints(result)
        return result

    # Handle single comparison operators
    key, value = parse_single_comparison(range_str)
    result[key] = value
    validate_range_constraints(result)
    return result
