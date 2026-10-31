"""
The tags of a wheel file name: the two axes a flat page spreads the wheels on.

A wheel is named `{name}-{version}(-{build})?-{python}-{abi}-{platform}.whl`
(PEP 427). Neither the name nor the version holds a '-', so the three tag fields
are the last three dashes from the right, and every field is a dot separated
set: `py2.py3-none-any` is two interpreters, the abi `none` and the platform
`any`.

    parse_tags('av-18.1.0-cp38-cp38-win_amd64.whl')
    # (('cp38',), ('cp38',), ('win_amd64',))

The matrix of a version has the platforms for rows and the python/abi pairs for
columns, so the module answers a key for both and the order of them:

    row_key(tags)               ('win_amd64',)      the platforms of a row
    row_display(row_key(tags))  'win_amd64'         the label of the row
    column_key('cp38', 'cp38')  'cp38'              the column of one tag

Both orders are total, so two runs of the same wheels render the same bytes and
a page that did not change is not written again.

Nothing here reads the page: a table sorts its keys once, with these functions,
and the objects render what the table holds.

Usage:
    from alasio.codegen.pypisite.flat import column_key, parse_tags, row_key

    tags = parse_tags('av-18.1.0-cp38-cp38-win_amd64.whl')
    row_key(tags)               # ('win_amd64',)
    column_key('cp38', 'cp38')  # 'cp38'
"""

__all__ = [
    'column_key',
    'column_sort_key',
    'is_pure',
    'parse_tags',
    'row_display',
    'row_key',
    'row_sort_key',
    'tag_label',
]

# The families of a platform tag, in the order the rows of a matrix are shown:
# the row of a platform starts with one of these prefixes
ROW_FAMILIES = (
    ('manylinux', 0),
    ('musllinux', 1),
    ('macosx', 2),
    ('win', 3),
    ('ios', 4),
)

# The rank of a platform that belongs to no family above, it sorts last
ROW_FAMILY_OTHER = 5

# The rank of the columns of an interpreter that is neither CPython nor PyPy,
# such as the 'py3' of a wheel that runs on any interpreter
COLUMN_FAMILY_OTHER = 2

# The abi tags that are not a build of one interpreter: a stable abi wheel and
# a wheel of pure python. A free-threaded build is the abi that ends with 't'
# and is neither of them.
ABI_NOT_A_BUILD = ('abi3', 'none')


def parse_tags(filename):
    """
    Split a wheel file name into its three tag fields.

    The name and the version of the file are not looked at: a WheelLink
    validated them already, and the field the table needs is the tail.

    Args:
        filename (str): File name of a wheel, e.g.
            'av-18.1.0-cp38-cp38-win_amd64.whl'

    Returns:
        tuple: (python, abi, platform), each one a tuple of the tags of the
            field, in the order the file spells them and without a repeat

    Raises:
        ValueError: If the file is not a wheel, if it has fewer than three tag
            fields, or if a field holds an empty tag
    """
    stem, sep, suffix = filename.rpartition('.')
    if not sep or suffix != 'whl':
        raise ValueError(f'The flat page shows wheels, this file is not one: {filename!r}')
    parts = stem.rsplit('-', 3)
    if len(parts) != 4:
        raise ValueError(f'The file name of a wheel holds three tag fields: {filename!r}')
    fields = []
    for field, text in (('python', parts[1]), ('abi', parts[2]), ('platform', parts[3])):
        tags = tuple(dict.fromkeys(text.split('.')))
        if not all(tags):
            raise ValueError(
                f'The {field} tags of the wheel are not dot separated tags: {filename!r}')
        fields.append(tags)
    return tuple(fields)


def is_pure(tags):
    """
    Whether a wheel of these tags installs on any platform.

    The answer reads the two fields themselves: a wheel of pure python declares
    the abi `none` and the platform `any`, and nothing else in the file name
    says it.

    Args:
        tags (tuple): The three fields from parse_tags

    Returns:
        bool: True for a wheel of pure python
    """
    _python, abi, platform = tags
    return abi == ('none',) and platform == ('any',)


def tag_label(tags):
    """
    Name every tag of a wheel the way a reader of the page names it.

    Args:
        tags (tuple): The three fields from parse_tags

    Returns:
        str: 'interpreter-abi-platform' of every combination, sorted and
            unique, e.g. 'py2-none-any, py3-none-any'
    """
    python, abi, platform = tags
    labels = {f'{interpreter}-{one_abi}-{one_platform}'
              for interpreter in python for one_abi in abi for one_platform in platform}
    return ', '.join(sorted(labels))


def column_key(interpreter, abi):
    """
    The column a tag of a matrix belongs to.

    A column is one interpreter, except for the free-threaded builds: they share
    the interpreter tag with the build that holds the GIL (`cp314` and `cp314t`),
    so only the abi tells them apart, and the abi becomes the column.

    Args:
        interpreter (str): Python tag, e.g. 'cp38'
        abi (str): Abi tag of the same wheel, e.g. 'cp38', 'abi3' or 'none'

    Returns:
        str: The column key, e.g. 'cp38', 'cp314t' or 'py3'
    """
    if interpreter.startswith('pp'):
        return interpreter
    if abi not in ABI_NOT_A_BUILD and abi.endswith('t'):
        return abi
    return interpreter


def _interpreter_order(digits):
    """
    Turn the digits of an interpreter tag into the version they name.

    A tag spells the version as its digits: 'cp38' is 3.8 and 'cp314' is 3.14,
    so the first digit is the major and the rest is the minor of it.

    Args:
        digits (str): Digits of an interpreter tag, e.g. '314'

    Returns:
        tuple: (major, minor), (0, 0) when the digits name no version
    """
    if not digits or not digits.isdigit():
        return 0, 0
    if len(digits) == 1:
        return int(digits), 0
    return int(digits[0]), int(digits[1:])


def column_sort_key(column):
    """
    Sort key of a column: CPython, then PyPy, then the rest, each by version.

    The versions compare as numbers, so 'cp39' comes before 'cp310'. Two builds
    of one version put the one that holds the GIL first: 'cp314' before
    'cp314t'.

    Args:
        column (str): Column key, see column_key

    Returns:
        tuple: Comparable key, total thanks to the column itself at the end
    """
    free_threaded = column.startswith('cp') and column.endswith('t')
    interpreter = column[:-1] if free_threaded else column
    if interpreter.startswith('cp'):
        family = 0
    elif interpreter.startswith('pp'):
        family = 1
    else:
        return COLUMN_FAMILY_OTHER, (0, 0), 0, column
    return family, _interpreter_order(interpreter[2:]), int(free_threaded), column


def row_key(tags):
    """
    The row a wheel of these tags belongs to: the set of its platforms.

    A wheel can carry several platform tags (`manylinux2014_aarch64` and
    `manylinux_2_17_aarch64` name one platform), so the key is the sorted set of
    them: two files that name the same set land in one row, whatever the order
    the file spells them in.

    Args:
        tags (tuple): The three fields from parse_tags

    Returns:
        tuple: The platform tags, sorted and unique
    """
    _python, _abi, platform = tags
    return tuple(sorted(platform))


def row_display(key):
    """
    The label of a row: its platforms joined with a dot.

    Args:
        key (tuple): Row key from row_key

    Returns:
        str: e.g. 'manylinux_2_17_x86_64.manylinux2014_x86_64'
    """
    return '.'.join(key)


def _token_order(text):
    """
    Split a tag into the tokens that compare by number, not by text.

    Without it 'musllinux_1_10' sorts before 'musllinux_1_2' and macOS 10.13
    before 10.9. A run of digits becomes a number and every other run a text,
    so the comparison needs no table of the versions of each family.

    Args:
        text (str): One component of a platform tag

    Returns:
        tuple: (0, int) for a run of digits and (1, str) for the other runs
    """
    tokens = []
    number = []
    letters = []
    for char in text:
        if char.isdigit():
            if letters:
                tokens.append((1, ''.join(letters)))
                letters = []
            number.append(char)
        else:
            if number:
                tokens.append((0, int(''.join(number))))
                number = []
            letters.append(char)
    if number:
        tokens.append((0, int(''.join(number))))
    if letters:
        tokens.append((1, ''.join(letters)))
    return tuple(tokens)


def row_sort_key(key):
    """
    Sort key of a platform row: the family, the version of it, then the label.

    The family order puts the platforms of the Linux distributions together,
    then the ones of macOS, Windows and iOS; a platform of no family sorts last.
    Inside a family the first component of the key orders, and the label of the
    row decides the rest, so the order is total.

    Args:
        key (tuple): Row key from row_key

    Returns:
        tuple: Comparable key
    """
    display = row_display(key)
    rank = ROW_FAMILY_OTHER
    for family, family_rank in ROW_FAMILIES:
        if display.startswith(family):
            rank = family_rank
            break
    first = key[0] if key else ''
    return rank, _token_order(first), display
