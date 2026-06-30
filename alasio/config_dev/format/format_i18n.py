from alasio.backport import removeprefix, removesuffix


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


def remove_setting_i18n(text):
    """
    Remove the word "setting" from text in various languages and ignore case

    "主线图设置" -> "主线图"
    "Opsi Settings" -> "Opsi"
    "Ajustes de Universo Simulado" -> "Universo Simulado"
    "依頼設定" -> "依頼"
    "任務設置" -> "任務"

    Args:
        text (str):

    Returns:
        str:
    """
    for word in ['設定', '设置', '設置']:
        text = removeprefix(text, word)
        text = removesuffix(text, word)
    lower = text.lower()
    for word in [
        'settings', 'setting',
        'ajustes de', 'ajuste de', 'ajustes', 'ajuste',
        'opciones de', 'opcion de', 'opciones', 'opcion',
    ]:
        # ignore case but preserve the case of the rest of the text
        if lower.startswith(word):
            text = text[len(word):].lstrip()
            lower = text.lower()
        if lower.endswith(word):
            text = text[:-len(word)].rstrip()
            lower = text.lower()
    return text