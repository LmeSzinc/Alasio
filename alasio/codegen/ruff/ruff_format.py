import os
import re
from pathlib import Path
import isort

from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.concurrent.cmd import parse_result, run_cmd
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write
from alasio.ext.path.calc import get_suffix

RUFF_RULES = """
# import rules
# I001	unsorted-imports	Import block is un-sorted or un-formatted
I002	missing-required-import	Missing required import: {name}
F401	unused-import	{name} imported but unused; consider using importlib.util.find_spec to test for availability
E401	multiple-imports-on-one-line	Multiple imports on one line
# white spaces before/after symbols
E2
# blank lines before/after class and functions
E3
# black lines at file end
W2
W3
# minor styling
E703	useless-semicolon	Statement ends with an unnecessary semicolon
E711	none-comparison	Comparison to None should be `cond is None`
E713	not-in-test	Test for membership should be `not in`
E714	not-is-test	Test for object identity should be `is not`
G010	logging-warn	Logging statement uses warn instead of warning
F901	raise-not-implemented	raise NotImplemented should be raise NotImplementedError
"""
REGEX_MODULE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
PY_SUFFIX = {'.py', '.pyi', '.pyx'}


def is_valid_module(name):
    if not REGEX_MODULE.match(name):
        return False
    if name == '__pycache__':
        return False
    return True


def is_valid_py(file):
    suffix = get_suffix(file).lower()
    return suffix in PY_SUFFIX


class RuffFormatter:
    LINE_LENGTH = 120

    def __init__(self):
        _ = self.cwd
        _ = self.rules
        _ = self.known_first_party
        _ = self.ruff_config
        print()

    @staticmethod
    def _get_cwd():
        # maybe PROJECT_ROOT is set
        try:
            from alasio.ext import env
            if env.PROJECT_ROOT:
                return env.PROJECT_ROOT
        except Exception:
            pass
        # maybe module/__init__.py has `env.set_project_root(...)`
        try:
            from module import env
            if env.PROJECT_ROOT:
                return env.PROJECT_ROOT
        except Exception:
            pass
        # maybe module/config/const.py has `entry.root = ...`
        try:
            from module.config.const import entry
            if entry.root:
                return entry.root
        except Exception:
            pass
        # just cwd
        return os.getcwd()

    @cached_property
    def cwd(self):
        cwd = PathStr.new(self._get_cwd())
        print(f'Ruff format cwd: {cwd}')
        return cwd

    @cached_property
    def rules(self):
        """
        Returns:
            str: I001,I002,F401,E401,...
        """
        rules = []
        for line in RUFF_RULES.splitlines():
            if not line or line.startswith('#'):
                continue
            left = line.partition('\t')[0]
            left = left.partition(' ')[0]
            rules.append(left)
        rules = ','.join(rules)
        print(f'Ruff rules: {rules}')
        return rules

    @cached_property
    def known_first_party(self):
        """
        Returns:
            str: assets,module,...
        """
        modules = []
        for folder in self.cwd.iter_foldernames():
            if is_valid_module(folder):
                modules.append(folder)
        for file in self.cwd.iter_files('.py'):
            stem = file.stem
            if is_valid_module(stem):
                modules.append(stem)
        modules = ','.join(modules)
        print(f'Ruff known-first-party: {modules}')
        return modules

    @cached_property
    def ruff_config(self):
        """
        Returns:
            str: ruff config in toml
        """
        lines = []
        lines.append(f'line-length = {self.LINE_LENGTH}')

        def to_list(text: str):
            items = [f'"{item}"' for item in text.split(',')]
            items = ', '.join(items)
            return f'[{items}]'

        lines.append('[lint]')
        lines.append(f'select = {to_list(self.rules)}')

        lines.append('[lint.isort]')
        lines.append('combine-as-imports = true')
        lines.append(f'known-first-party = {to_list(self.known_first_party)}')

        return '\n'.join(lines)

    def format_code(self, file: str):
        if os.path.isabs(file):
            try:
                filename = Path(file).relative_to(Path(self.cwd))
            except ValueError:
                filename = file
        else:
            filename = file

        print(f'Formatting: {filename}')
        code = atomic_read_bytes(file)
        origin = code
        temp_config_file = self.cwd / '_temp_config.toml'
        cmd = [
            'ruff', 'check', '-', '--fix',
            '--preview',  # enable E2 E3 W3
            '--stdin-filename', filename,
            '--select', self.rules,
            '--config', temp_config_file,
        ]

        # run_cmd
        # write config to temp file to avoid command too long
        temp_config_file.file_write(self.ruff_config)
        try:
            result = run_cmd(cmd, text=False, strip=False, check=False, input=code)
        finally:
            temp_config_file.file_remove()
        result.stderr = parse_result(result.stderr)
        if result.stdout:
            code = result.stdout

        # sort imports
        # since import sorting in ruff is kind of strict, we use isort
        code = code.decode('utf-8')
        new = isort.api.sort_code_string(
            code,
            line_length=self.LINE_LENGTH,
            multi_line_output=5,
            combine_as_imports=True,
            show_diff=False,
            quiet=False,
        )
        if new == code:
            print('Import sorting all good')
        else:
            print('Import sorting fixed')
            code = new.encode('utf-8')

        # write file if changed
        if code != origin:
            print(f'Writing: {file}')
            atomic_write(file, code)
        print(result.stderr)
        print()


if __name__ == '__main__':
    env.set_project_root(__file__, up=4)
    self = RuffFormatter()
    self.format_code(r'')
