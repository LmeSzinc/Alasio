KEY_at = ' - at `'
KEY_got = ', got '


def _path_split_key(path):
    """
    Args:
        path (str): [...].RepairThreshold[0].value

    Yields:
        str: ..., RepairThreshold[0].value
    """
    if '[...]' in path:
        for index, part in enumerate(path.split('[...]')):
            if index:
                yield '...'
            if part:
                yield part
    # no dict key
    else:
        yield path


def _path_split_index(path):
    """
    Args:
        path (str): RepairThreshold[0].value

    Yields:
        int | str:
    """
    prefix = ''
    while 1:
        field, sep, remain = path.partition('[')
        if not sep:
            break
        index, sep, remain = remain.partition(']')
        if not sep:
            break
        try:
            index = int(index)
            # [0]
            if prefix:
                yield prefix + field
            elif field:
                yield field
            yield index
            path = remain
        except ValueError:
            # Invalid index like: [abc], [[], []]
            # skip them and check the remains
            prefix = f'{field}[{index}]'
            path = remain

    # no list index
    if path:
        yield path


def _path_split_part(path):
    """
    Args:
        path (str): OpsiGeneral.RepairThreshold

    Yields:
        str:
    """
    if '.' in path:
        for part in path.split('.'):
            if part:
                yield part
    else:
        yield path


def _path_split(path):
    """
    Args:
        path (str): Note that $. and $ should be removed before input
            OpsiGeneral.RepairThreshold
            [...].RepairThreshold[0].value

    Yields:
        str | int:
    """
    for field_index_key in _path_split_key(path):
        if field_index_key == '...':
            yield field_index_key
            continue
        for field_index in _path_split_index(field_index_key):
            if type(field_index) is int:
                yield field_index
                continue
            yield from _path_split_part(field_index)


def get_error_key(error):
    """
    Args:
        error (str):

    Returns:
        str:
    """
    # Object contains unknown field `RepairThresho` ld1` - at `$.opsi`
    # Object missing required field `id`
    if error.startswith('Object missing required field '):
        error = error[30:]
    elif error.startswith('Object contains unknown field '):
        error = error[30:]
    else:
        return ''

    # remove path and leave key
    # don't just do re.search(`(.*?)`, ...)
    # because msgspec error message don't have any escape on special characters
    if KEY_at in error:
        error, _, _ = error.rpartition(KEY_at)

    # remove paired ``
    if len(error) >= 2 and error.startswith('`') and error.endswith('`'):
        error = error[1:-1]
    return error


def get_error_path(error):
    """
    Args:
        error (str):

    Returns:
        tuple[Union[int, str]]
    """
    key = get_error_key(error)
    if KEY_at in error:
        # Expected `MyCustomClass`, got `str` - at `$.custom_field`
        reason, _, error = error.rpartition(KEY_at)
        if error.endswith('`'):
            error = error[:-1]
        # $.field
        if error.startswith('$.'):
            error = error[2:]
        # $[0]
        elif error.startswith('$'):
            error = error[1:]

        # pydantic style that tells you ('custom_field', 'id') is missing
        if key:
            path = list(_path_split(error))
            path.append(key)
            return tuple(path)
        else:
            return tuple(_path_split(error))
    else:
        # no path
        if key:
            return (key,)
        else:
            return ()
