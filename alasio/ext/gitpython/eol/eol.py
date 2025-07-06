import msgspec

from alasio.ext.path.atomic import atomic_read_text
from alasio.ext.gitpython.eol.const import *


class AttrInfo(msgspec.Struct):
    pattern: str
    is_binary: bool = False
    is_text: bool = False
    eol_lf: bool = False
    eol_crlf: bool = False

    @property
    def valid(self):
        if self.pattern == '*':
            return False
        if self.is_binary or self.is_text or self.eol_lf or self.eol_crlf:
            return True
        return False


def parse_attr_info(parts):
    """
    Args:
        parts (list[str]):

    Returns:
        AttrInfo:
    """
    pattern = parts[0]
    attr = parts[1:]
    is_binary = False
    is_text = False
    eol_lf = False
    eol_crlf = False

    if 'binary' in attr:
        is_binary = True
    elif 'text' in attr:
        is_text = True
    if 'eol=lf' in attr:
        eol_lf = True
    elif 'eol=crlf' in attr:
        eol_crlf = True

    return AttrInfo(
        pattern=pattern,
        is_binary=is_binary,
        is_text=is_text,
        eol_lf=eol_lf,
        eol_crlf=eol_crlf,
    )


class GitAttribute:
    def __init__(self, file):
        """
        Args:
            file (str): filepath to .gitattribute
        """
        self.file = file
        # key: pattern, value: AttrInfo object
        self.dict_eol: "dict[str, AttrInfo]" = {}

    def read(self):
        # Dynamic import to avoid global import re
        import shlex

        data = atomic_read_text(self.file)
        dict_eol = {}

        for row in data.splitlines():
            row = row.strip()
            # empty or comment
            if not row or row.startswith('#'):
                continue
            try:
                # shlex.split handles basic quoting for patterns with spaces
                parts = shlex.split(row)
            except ValueError:
                continue
            if len(parts) < 2:
                continue
            # parse
            info = parse_attr_info(parts)
            if not info.valid:
                continue
            dict_eol[info.pattern] = info

        self.dict_eol = dict_eol


def eol_crlf_remove(path, data):
    """
    Args:
        path (str):
        data (bytes):

    Returns:
        bytes:
    """
    # suffix
    _, _, suffix = path.rpartition('.')
    if suffix in SET_BINARY_SUFFIX:
        return data
    if suffix in SET_TEXT_SUFFIX:
        return data.replace(b'\r\n', b'\n')
    # name
    _, _, name = path.rpartition('/')
    if name in SET_BINARY_NAME:
        return data
    if name in SET_TEXT_NAME:
        return data.replace(b'\r\n', b'\n')
    # predict if binary
    if b'\x00' in data:
        return data
    else:
        return data.replace(b'\r\n', b'\n')


def eol_crlf_readd(path, data):
    """
    Args:
        path (str):
        data (bytes):

    Returns:
        bytes:
    """
    # suffix
    _, _, suffix = path.rpartition('.')
    if suffix in SET_BINARY_SUFFIX:
        return data
    if suffix in SET_TEXT_SUFFIX:
        return data.replace(b'\n', b'\r\n')
    # name
    _, _, name = path.rpartition('/')
    if name in SET_BINARY_NAME:
        return data
    if name in SET_TEXT_NAME:
        return data.replace(b'\n', b'\r\n')
    # predict if binary
    if b'\x00' in data:
        return data
    else:
        return data.replace(b'\n', b'\r\n')
