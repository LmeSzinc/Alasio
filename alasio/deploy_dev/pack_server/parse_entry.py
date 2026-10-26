"""
Parse the update_pack_deps declaration from the source of a mod entry.

The pack server builds the dependency channels a mod declares for pack
updates. The declaration lives in the mod entry, the module-level variable
`entry` of `module/config/const.py`:

    from alasio.config.entry.const import ModEntryInfo

    entry = ModEntryInfo(
        name='alas',
        update_pack_deps=('httpx', 'ruamel.yaml'),
    )

The pack server reads the source from the git blob of a commit, not from a
working tree, and it must not execute code of the repo it packs. This module
provides the reader of the blob and the parser of the declaration:

- PackRepoEntry.update_pack_deps(commit) reads module/config/const.py from
the commit and parses it, caching the result by the blob sha1;
- parse_update_pack_deps(source) parses the source itself, with ast: the
source is never imported, compiled into a callable or evaluated. Only the
`update_pack_deps` field of the entry is read, the other fields and the other
statements of the module are ignored.

The declaration may be the keyword of the entry construction or an attribute
assignment after it:

    entry = ModEntryInfo(name='alas')
    entry.update_pack_deps = ('httpx',)

The entry may also be one of the default entries of the library, looked up by
key:

    from alasio.config.entry.const import DICT_MOD_ENTRY

    entry = DICT_MOD_ENTRY['alas']
    entry.update_pack_deps = ('httpx',)

The lookup is resolved against the library's own DICT_MOD_ENTRY, never against
the source of the repo: the source is not imported or executed. The base
entry's update_pack_deps is the starting value, a later attribute assignment
replaces it, and a later ModEntryInfo construction resets it.

The value must be a static expression, read in the order of the source:

- a dict literal, only its keys are used;
- a list, tuple or set literal;
- dict.fromkeys() over one of the forms above;
- a module-level name that was assigned one of the forms above.

A later assignment to entry resets the declaration, a later assignment to
entry.update_pack_deps replaces it. A value of None, an empty dict / list /
tuple / set or a missing update_pack_deps means no dependency is updated by
pack. The names are returned as they are written, the caller normalizes them
with PEP 503, like the other names of the pack files.

Everything else raises ValueError: a dynamic expression, a mutation of
entry.update_pack_deps after the declaration (e.g. .update() or an item
assignment), an entry built by a helper call that does not name the keyword, a
DICT_MOD_ENTRY lookup whose name is not imported from the library module, whose
key is not a static str of the library dict, or a declaration outside the module
level. A pack must never silently lose a dependency channel because this parser
could not read the declaration.

Usage:
    from alasio.deploy_dev.pack_server.parse_entry import PackRepoEntry, parse_update_pack_deps

    names = parse_update_pack_deps(source, source_name='module/config/const.py at c3')
    names = PackRepoEntry(repo).update_pack_deps(commit)
"""

import ast

from alasio.config.entry.const import DICT_MOD_ENTRY as _LIBRARY_MOD_ENTRY

# Path of the mod entry in a repo, the file ModLoader.self_mod reads. It is
# kept here so the caller that reads the git blob and the parser agree on the
# layout of a mod.
ENTRY_FILE = 'module/config/const.py'

# module of the library default entries, the only module a DICT_MOD_ENTRY
# lookup is resolved from
_ENTRY_CONST_MODULE = 'alasio.config.entry.const'

# Sentinel of a module level name whose value is not a static expression. The
# name is recorded so a reference to it reports the unsupported value instead
# of an unknown name.
_UNKNOWN = object()


class _Unsupported(ValueError):
    """
    Raised inside the parser for a source form it cannot read.

    The public function adds the name of the source to the message.
    """

    def __init__(self, message, node=None):
        """
        Args:
            message (str): Reason of the error
            node (ast.AST, optional): Node the error was found at, its line
                number is appended to the message. Defaults to None.
        """
        lineno = getattr(node, 'lineno', None)
        if lineno:
            message = f'{message} at line {lineno}'
        super().__init__(message)


def parse_update_pack_deps(source, source_name=''):
    """
    Parse the update_pack_deps declaration of a mod entry.

    The source is parsed with ast and never executed. See the module
    docstring for the accepted declarations.

    Args:
        source (str | bytes): Python source of the entry module. A bytes
            source is decoded by ast with the encoding declaration it
            carries, like Python itself
        source_name (str): Name of the source used in the error messages,
            e.g. 'module/config/const.py at commit c3'. Defaults to '',
            the message only carries the reason

    Returns:
        tuple[str, ...]: The declared distribution names in source order,
            empty when the module declares no entry or an empty
            update_pack_deps

    Raises:
        ValueError: If the source is not valid Python, the entry declaration
            cannot be read, or update_pack_deps is not a static collection of
            str
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        message = f'invalid Python at line {e.lineno}: {e.msg}'
        raise ValueError(_with_source_name(source_name, message)) from e

    parser = _EntryParser()
    try:
        parser.visit(tree)
    except _Unsupported as e:
        raise ValueError(_with_source_name(source_name, str(e))) from None
    return parser.update_pack_deps


def _with_source_name(source_name, message):
    """
    Prefix an error message with the name of the source, when there is one.

    Args:
        source_name (str): Name of the source, empty for none
        message (str): Error message

    Returns:
        str: Message with the source name
    """
    if source_name:
        return f'{source_name}: {message}'
    return message


class PackRepoEntry:
    """
    Read the update_pack_deps of a mod entry from the commits of a git repo.

    The entry file is read from the git blob of a commit, never from a
    working tree: the pack server clones a repo without a working tree. The
    parse result is cached by the blob sha1, so commits that share the entry
    file parse it once.

    Usage:
        entry = PackRepoEntry(repo)
        names = entry.update_pack_deps(commit)
    """

    def __init__(self, repo):
        """
        Args:
            repo (GitRepo | MockGitRepo): Repo to read; its object index is
                read lazily when the caller did not read it
        """
        self.repo = repo
        # blob sha1 -> tuple[str, ...]
        self._cache = {}

    def update_pack_deps(self, commit):
        """
        Read the update_pack_deps declaration of an entry from a commit.

        Args:
            commit (str): Commit sha1 to read the entry from

        Returns:
            tuple[str, ...]: Declared names in source order, empty when the
                commit has no entry file or the entry declares no name

        Raises:
            ValueError: If the entry source is not valid Python, its
                declaration cannot be read, or the entry path is a symlink
        """
        self._read_repo()
        file = self.repo.get_file(commit, ENTRY_FILE)
        if file is None:
            return ()
        if file.mode == b'120000':
            raise ValueError(
                f'{ENTRY_FILE} at commit {commit} is a symlink, not a file')
        cached = self._cache.get(file.sha1)
        if cached is not None:
            return cached
        source = self.repo.cat(file.sha1).decoded
        names = parse_update_pack_deps(
            source, source_name=f'{ENTRY_FILE} at commit {commit}')
        self._cache[file.sha1] = names
        return names

    def _read_repo(self):
        """
        Read the object index of the repo when the caller did not.

        GitRepo reads its objects with read_lazy(); an unread repo has no
        object to look up. MockGitRepo keeps its objects in memory and its
        read_lazy() is a no-op, the check is harmless for it.
        """
        if not (
                getattr(self.repo, 'dict_object', None)
                or getattr(self.repo, 'dict_object_unread', None)
        ):
            self.repo.read_lazy()


def _is_dict_fromkeys(node):
    """
    Check whether a call target is dict.fromkeys().

    Args:
        node (ast.AST): Call target to inspect

    Returns:
        bool: True for the attribute dict.fromkeys
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == 'fromkeys'
        and isinstance(node.value, ast.Name)
        and node.value.id == 'dict'
    )


def _is_mod_entry_info(func):
    """
    Check whether a call target names ModEntryInfo.

    The entry is built by ModEntryInfo in the usual form. The check accepts
    the bare name and a qualified name, e.g. `const.ModEntryInfo`.

    Args:
        func (ast.AST): Call target of the entry construction

    Returns:
        bool: True when the target ends with ModEntryInfo
    """
    if isinstance(func, ast.Name):
        return func.id == 'ModEntryInfo'
    if isinstance(func, ast.Attribute):
        return func.attr == 'ModEntryInfo'
    return False


def _is_update_pack_deps_attr(node):
    """
    Check whether an expression is the entry.update_pack_deps attribute.

    Args:
        node (ast.AST): Expression to inspect

    Returns:
        bool: True for entry.update_pack_deps
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == 'update_pack_deps'
        and isinstance(node.value, ast.Name)
        and node.value.id == 'entry'
    )


def _is_update_pack_deps_member(node):
    """
    Check whether an expression is a member or an item of update_pack_deps.

    The forms are entry.update_pack_deps.update, entry.update_pack_deps['x']
    and an attribute of them, the mutations the parser refuses.

    Args:
        node (ast.AST): Expression to inspect

    Returns:
        bool: True when the expression is rooted at entry.update_pack_deps
    """
    if isinstance(node, ast.Attribute):
        return _is_update_pack_deps_attr(node.value)
    if isinstance(node, ast.Subscript):
        return _is_update_pack_deps_attr(node.value)
    return False


def _mentions_entry(node):
    """
    Check whether a statement mentions the entry or update_pack_deps.

    It is used on the statements the parser does not read, e.g. a conditional
    block or an import, so a declaration hidden there fails loudly instead of
    silently producing an empty list. A plain use of entry, e.g. setting
    entry.root, does not count: only an assignment to the entry name, an
    update_pack_deps keyword or attribute, and an import of entry do.

    Args:
        node (ast.AST): Statement to inspect

    Returns:
        bool: True when the statement may declare the entry
    """
    for child in ast.walk(node):
        if isinstance(child, ast.keyword) and child.arg == 'update_pack_deps':
            return True
        if isinstance(child, ast.Attribute) and child.attr == 'update_pack_deps':
            return True
        if isinstance(child, ast.alias) and child.name == 'entry':
            return True
        if isinstance(child, ast.Assign):
            targets = child.targets
        elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
            targets = (child.target,)
        else:
            continue
        if any(
                isinstance(target, ast.Name) and target.id == 'entry'
                for target in targets
        ):
            return True
    return False


def _coerce_names(value, node=None):
    """
    Convert a static update_pack_deps value into a tuple of names.

    Args:
        value: Evaluated value of the declaration
        node (ast.AST, optional): Node the value came from, for the error
            message. Defaults to None.

    Returns:
        tuple[str, ...]: Declared names in their order, deduplicated

    Raises:
        _Unsupported: If the value is not a collection of str
    """
    if value is None:
        return ()
    if isinstance(value, dict):
        names = list(value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        names = list(value)
    else:
        raise _Unsupported(
            'update_pack_deps must be a dict, list, tuple or set of str, '
            f'got {type(value).__name__}', node)

    out = {}
    for name in names:
        if not isinstance(name, str):
            raise _Unsupported(
                f'update_pack_deps names must be str, got {type(name).__name__}', node)
        out[name] = None
    return tuple(out)


class _EntryParser:
    """
    Walk the module level statements of an entry module.

    The parser keeps the module level names it can evaluate, the local names
    of the library entry imports, and the last update_pack_deps declaration it
    read. Only module level assignments are read, see _mentions_entry for the
    guard of the other statements.
    """

    def __init__(self):
        # module level names that hold a static value, {name: value}
        self.constants = {}
        # local names bound to the library DICT_MOD_ENTRY by an import
        self.dict_mod_entry_names = set()
        # local names bound to the library entry module by an import
        self.entry_module_names = set()
        # last declaration read, tuple[str, ...]
        self.update_pack_deps = ()

    def visit(self, tree):
        """
        Read the module level statements of a parsed module.

        Args:
            tree (ast.Module): Parsed source

        Raises:
            _Unsupported: If a declaration cannot be read
        """
        for node in tree.body:
            self._visit_statement(node)

    def _visit_statement(self, node):
        """
        Read one module level statement.

        Args:
            node (ast.stmt): Statement to read

        Raises:
            _Unsupported: If the statement declares the entry in a form the
                parser cannot read
        """
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._assign(target, node.value)
            return

        if isinstance(node, ast.AnnAssign):
            if node.value is None:
                if isinstance(node.target, ast.Name) and node.target.id == 'entry':
                    raise _Unsupported('entry has no value', node)
                return
            self._assign(node.target, node.value)
            return

        if isinstance(node, ast.AugAssign):
            if _is_update_pack_deps_attr(node.target):
                raise _Unsupported(
                    'augmented assignment to update_pack_deps is not supported', node)
            self._check_other(node)
            return

        if isinstance(node, ast.Delete):
            if any(
                    _is_update_pack_deps_attr(target) or _is_update_pack_deps_member(target)
                    for target in node.targets
            ):
                raise _Unsupported('deleting update_pack_deps is not supported', node)
            self._check_other(node)
            return

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            self._record_import(node)
        self._check_other(node)

    def _check_other(self, node):
        """
        Refuse a statement the parser does not read that mentions the entry.

        Args:
            node (ast.stmt): Statement to check

        Raises:
            _Unsupported: If the statement may declare the entry
        """
        if _mentions_entry(node):
            raise _Unsupported(
                'entry and update_pack_deps must be declared by module level assignments',
                node)

    def _record_import(self, node):
        """
        Record the local names of the library entry imports.

        Only the module that holds the default entries is accepted: a
        DICT_MOD_ENTRY imported from another module is not the library dict,
        and the parser must not resolve it to the library entry.

        Args:
            node (ast.Import | ast.ImportFrom): Import statement to read
        """
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module == _ENTRY_CONST_MODULE:
                for alias in node.names:
                    if alias.name == 'DICT_MOD_ENTRY':
                        self.dict_mod_entry_names.add(alias.asname or alias.name)
            return
        for alias in node.names:
            if alias.name == _ENTRY_CONST_MODULE and alias.asname:
                self.entry_module_names.add(alias.asname)

    def _is_dict_mod_entry(self, node):
        """
        Check whether an expression is the library DICT_MOD_ENTRY.

        The accepted forms are the name imported from the library entry
        module, e.g. DICT_MOD_ENTRY or an alias of it, and the attribute of a
        module imported as an alias, e.g. const.DICT_MOD_ENTRY.

        Args:
            node (ast.AST): Expression to inspect

        Returns:
            bool: True when the expression is the library DICT_MOD_ENTRY
        """
        if isinstance(node, ast.Name):
            return node.id in self.dict_mod_entry_names
        if isinstance(node, ast.Attribute):
            return (
                node.attr == 'DICT_MOD_ENTRY'
                and isinstance(node.value, ast.Name)
                and node.value.id in self.entry_module_names
            )
        return False

    def _eval_subscript_key(self, node):
        """
        Evaluate the key of a subscript, across the ast of python 3.8.

        In python 3.8 the slice of a subscript is an ast.Index that wraps the
        expression; from python 3.9 on it is the expression itself.

        Args:
            node (ast.AST): Slice of a subscript

        Returns:
            The static value of the key

        Raises:
            _Unsupported: If the key is not a static expression
        """
        index_type = getattr(ast, 'Index', None)
        if index_type is not None and isinstance(node, index_type):
            node = node.value
        return self._eval(node)

    def _assign_dict_mod_entry(self, value):
        """
        Read an entry assigned from the library DICT_MOD_ENTRY.

        The lookup is resolved by the key in the library dict, never by the
        source of the repo: the entry module is not imported or executed. The
        key must be a static str, and the base value is the entry's
        update_pack_deps.

        Args:
            value (ast.Subscript): The DICT_MOD_ENTRY[key] expression

        Raises:
            _Unsupported: If the expression is not the library DICT_MOD_ENTRY,
                the key is not a static str, or the key is unknown
        """
        if not self._is_dict_mod_entry(value.value):
            raise _Unsupported(
                'entry must be constructed by a call or a DICT_MOD_ENTRY lookup', value)
        key = self._eval_subscript_key(value.slice)
        if not isinstance(key, str):
            raise _Unsupported(
                f'DICT_MOD_ENTRY key must be a str, got {type(key).__name__}', value)
        try:
            entry = _LIBRARY_MOD_ENTRY[key]
        except KeyError:
            raise _Unsupported(f'unknown DICT_MOD_ENTRY key "{key}"', value) from None
        self.update_pack_deps = tuple(entry.update_pack_deps)

    def _assign(self, target, value):
        """
        Read one assignment target.

        Args:
            target (ast.AST): Target of the assignment
            value (ast.AST): Value of the assignment

        Raises:
            _Unsupported: If the target is a declaration the parser cannot
                read
        """
        if isinstance(target, ast.Name):
            if target.id == 'entry':
                self._assign_entry(value)
            else:
                self._record_constant(target.id, value)
            return

        if _is_update_pack_deps_attr(target):
            self.update_pack_deps = _coerce_names(self._eval(value), value)
            return

        if _is_update_pack_deps_member(target):
            raise _Unsupported(
                'assignment to an item of update_pack_deps is not supported', target)

        # other attributes and items, e.g. entry.root or a plain dict item,
        # do not declare the dependency names

    def _assign_entry(self, value):
        """
        Read an assignment to the entry name.

        The value is an entry construction or a lookup of a default entry of
        the library, see the module docstring.

        Args:
            value (ast.AST): Value of the assignment

        Raises:
            _Unsupported: If the value is not an entry construction or a
                DICT_MOD_ENTRY lookup the parser can read
        """
        if isinstance(value, ast.Subscript):
            self._assign_dict_mod_entry(value)
            return
        if not isinstance(value, ast.Call):
            raise _Unsupported('entry must be constructed by a call', value)
        if any(keyword.arg is None for keyword in value.keywords):
            raise _Unsupported('**kwargs in the entry construction is not supported', value)

        keyword = next(
            (keyword for keyword in value.keywords if keyword.arg == 'update_pack_deps'),
            None,
        )
        if keyword is None:
            # an entry without the declaration is valid, but only when the
            # construction is the known ModEntryInfo: a helper may set the
            # declaration in its own body, where this parser cannot see it
            if not _is_mod_entry_info(value.func):
                raise _Unsupported(
                    'entry is constructed by a call that does not declare update_pack_deps',
                    value)
            self.update_pack_deps = ()
            return

        self.update_pack_deps = _coerce_names(self._eval(keyword.value), keyword.value)

    def _record_constant(self, name, value):
        """
        Record a module level name for a later reference.

        A value the parser cannot evaluate is recorded as unknown: the name is
        only an error when update_pack_deps actually references it, so an
        unrelated dynamic module level value does not fail the parse.

        Args:
            name (str): Name of the assignment target
            value (ast.AST): Value of the assignment
        """
        try:
            self.constants[name] = self._eval(value)
        except _Unsupported:
            self.constants[name] = _UNKNOWN

    def _eval(self, node):
        """
        Evaluate a static expression of the module.

        Args:
            node (ast.AST): Expression to evaluate

        Returns:
            The static value of the expression

        Raises:
            _Unsupported: If the expression is not static or not one of the
                forms the parser reads
        """
        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            try:
                value = self.constants[node.id]
            except KeyError:
                raise _Unsupported(f'unknown name "{node.id}"', node) from None
            if value is _UNKNOWN:
                raise _Unsupported(f'name "{node.id}" is not a static value', node)
            return value

        if isinstance(node, ast.Dict):
            keys = []
            for key in node.keys:
                if key is None:
                    raise _Unsupported('dict unpacking is not supported', node)
                keys.append(self._eval(key))
            try:
                return dict.fromkeys(keys, None)
            except TypeError as e:
                raise _Unsupported(f'dict key is not hashable: {e}', node) from None

        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [self._eval(element) for element in node.elts]

        if isinstance(node, ast.Call):
            if not _is_dict_fromkeys(node.func):
                raise _Unsupported(
                    'unsupported call, only dict.fromkeys() is read', node)
            if not 1 <= len(node.args) <= 2 or node.keywords:
                raise _Unsupported(
                    'dict.fromkeys() takes one or two positional arguments', node)
            items = self._eval(node.args[0])
            if not isinstance(items, (dict, list, tuple, set, frozenset)):
                raise _Unsupported(
                    'dict.fromkeys() requires a dict, list, tuple or set of names', node)
            return dict.fromkeys(items, None)

        raise _Unsupported(f'unsupported expression: {type(node).__name__}', node)
