import re
from collections import deque


def remove_hash_comment(s):
    """
    Remove python-like "#" comment in DSL

    Args:
        s (str):

    Returns:
        str:

    Examples:
        DailyEvent > Gem-8 > Gem-4 > Gem-2 > ExtraCube-0:30 # this is an inline comment
        > UrgentCube-1:30 > UrgentCube-1:45 > UrgentCube-3
        # this is a comment
        > UrgentCube-2:15 > UrgentCube-4 > UrgentCube-6
        > ExtraCube-1:30 > ExtraCube-3 > ExtraCube-4
        > ExtraCube-8 > UrgentBox-6 > UrgentBox-3 > ExtraCube-5 > UrgentBox-1
    """
    rows = deque()
    for row in s.splitlines():
        left, sep, _ = row.partition('#')
        if sep:
            row = left
        row = row.strip()
        if not row:
            continue
        rows.append(row)
    return '\n'.join(rows)


def parse_filter(s):
    """
    Parse filter DSL into tuple

    Args:
        s (str):

    Returns:
        tuple[str]:

    Examples:
        parse_filter('''
        DailyEvent > Gem-8 > Gem-4 > Gem-2 > ExtraCube-0:30 # this is an inline comment
        > UrgentCube-1:30 > UrgentCube-1:45 > UrgentCube-3
        # this is a comment
        > UrgentCube-2:15 > UrgentCube-4 > UrgentCube-6
        ''')
        # ("DailyEvent", "Gem-8", "Gem-4", ...)
    """
    s = str(s)
    s = remove_hash_comment(s)
    # remove \n
    s = re.sub(r'\s', '', s)
    # There are also tons of unicode characters similar to ">"
    # replace them to be fool-proof
    # > \u003E correct
    # ＞ \uFF1E
    # ﹥ \uFE65
    # › \u203a
    # ˃ \u02c3
    # ᐳ \u1433
    # ❯ \u276F
    s = re.sub(r'[＞﹥›˃ᐳ❯]', '>', s)
    items = [i for i in s.split('>') if i]
    return items


def split_option(item):
    """
    Split a filter item that carries a secondary option

    A dt="filter-order" item is either a primary item (`Chip`) or a primary
    item with one of its own secondary options (`PlateT4:gun`). The item is
    split on the first separator, so the secondary option keeps any later one.

    Args:
        item (str): Item of a filter-order value

    Returns:
        tuple[str, str]: (primary, secondary), secondary is None when the item
            carries no secondary option
    """
    primary, sep, secondary = str(item).partition(':')
    if not sep:
        return primary, None
    return primary, secondary
