import re

from alasio.ext.path.atomic import atomic_read_text, atomic_write


def may_int(string):
    if string.startswith('-') or string.startswith('+'):
        string = string[1:]
    return string.isdigit()


def may_float(string):
    if '.' not in string:
        return False
    # Check if first character is a digit,
    # not a rigorous check since float(string) would handle
    if string.startswith('-') or string.startswith('+') or string.startswith('.'):
        return string[1].isdigit()
    else:
        return string[0].isdigit()


def decode_value(string):
    """
    Args:
        string (str):

    Returns:
        Any:
    """
    string = string.strip()
    lower = string.lower()
    if lower == 'null':
        return None
    if lower == 'false':
        return False
    if lower == 'true':
        return True
    if may_int(string):
        try:
            return int(string)
        except ValueError:
            pass
    if may_float(string):
        try:
            return float(string)
        except ValueError:
            pass
    # list
    if string.startswith('[') and string.endswith(']'):
        items = string[1:-1].split(',')
        return [decode_value(item) for item in items]
    # fallback, treat as string
    return string


def encode_value(obj):
    """
    Args:
        obj (Any):

    Returns:
        str:
    """
    if obj is None:
        return 'null'
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    # fallback, encode as string
    return str(obj)


def poor_yaml_read(file):
    """
    Poor implementation to load yaml without pyyaml dependency, but with re

    Args:
        file (str):

    Returns:
        dict:
    """
    content = atomic_read_text(file)
    data = {}
    regex = re.compile(r'^(.*?):(.*?)$')
    for line in content.splitlines():
        line = line.strip()
        if line.startswith('#'):
            continue
        result = re.match(regex, line)
        if not result:
            continue
        k = result.group(1).strip()
        v = result.group(2)
        if v:
            data[k] = decode_value(v)

    return data


def poor_yaml_write(file, data, template_file, skip_same=False):
    """
    Args:
        file (str):
        data (dict):
        template_file (str):
        skip_same (bool):
            True to skip writing if existing content is the same as content to write.
            This would reduce disk write but add disk read

    Returns:
        bool: if write
    """
    text = atomic_read_text(template_file)
    old = text

    # Change values with re
    regex = re.compile(r'^(.*?):(.*?)$')
    for key, value in data.items():
        value = encode_value(value)
        text = regex.sub(f'{key}: {value}\n', text)

    if skip_same:
        if text == old:
            return False
        else:
            atomic_write(file, text)
            return True
    else:
        atomic_write(file, text)
        return True
