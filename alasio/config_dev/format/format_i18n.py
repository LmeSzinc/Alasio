def format_i18n(text):
    """
    Input any text with \n, or list of text with \n
    Output str if text is a row of text
        list[str] if text has multiple lines

    Args:
        text (str | list[str]):

    Returns:
        str | list[str]:
    """
    if not text:
        return ''
    rows = list(_iter_i18n(text))
    if not rows:
        return ''
    if len(rows) == 1:
        return rows[0]
    return rows


def _iter_i18n(text):
    """
    splitlines in i18n, iter rows

    Args:
        text (str | list[str]):

    Yields:
        str
    """
    t = type(text)
    if t is str:
        if '\n' in text:
            for row in text.splitlines():
                yield row.rstrip()
        else:
            yield text.rstrip()
    elif t is list:
        for row in text:
            yield from _iter_i18n(row)
    else:
        # unknown type, treat as str
        text = str(text)
        yield from _iter_i18n(text)
