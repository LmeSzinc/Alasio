"""A minimal stand-in for the `winreg` module.

The registry is described by a plain dict, which keeps the tests readable:

    tree = {
        r'Software\\Vendor\\Key': {
            'values': {'InstallDir': 'E:/Emulator'},
            'subkeys': ['Child'],
        },
    }

`OpenKey()` raises `FileNotFoundError` for a missing path and `EnumKey()` /
`EnumValue()` raise `OSError` past the end of a key, which is what the real
module does and what the `while 1: ... except OSError` loops of
`windows_reg.py` rely on.
"""


class RegKey:
    __slots__ = ('path', 'values', 'subkeys')

    def __init__(self, path, values=None, subkeys=None):
        self.path = path
        self.values = values or {}
        self.subkeys = subkeys or []

    def __repr__(self):
        return f'RegKey({self.path!r})'

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = 'HKEY_CURRENT_USER'
    HKEY_LOCAL_MACHINE = 'HKEY_LOCAL_MACHINE'

    def __init__(self, tree):
        self.tree = tree

    def _key(self, root, path):
        try:
            entry = self.tree[path]
        except KeyError:
            raise FileNotFoundError(2, 'system cannot find the file specified') from None
        return RegKey(path, entry.get('values'), entry.get('subkeys'))

    def OpenKey(self, root, path):
        return self._key(root, path)

    def QueryValueEx(self, key, name):
        try:
            value = key.values[name]
        except KeyError:
            raise FileNotFoundError(2, 'system cannot find the file specified') from None
        return value, 1

    def EnumKey(self, key, index):
        try:
            return key.subkeys[index]
        except IndexError:
            raise OSError(259, 'no more data is available') from None

    def EnumValue(self, key, index):
        try:
            name, value = list(key.values.items())[index]
        except IndexError:
            raise OSError(259, 'no more data is available') from None
        return name, value, 1
