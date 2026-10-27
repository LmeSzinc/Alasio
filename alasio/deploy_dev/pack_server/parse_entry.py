"""
Parse the declarations the pack server reads from the source of a mod entry.

The pack server builds the dependency channels a mod declares for pack
updates, fetches their wheels from the PyPI mirrors the mod declares, and
publishes the wheels of the indexes the mod provides. The declarations live in
the mod entry, the module-level variable `entry` of
`module/config/const.py`:

    from alasio.config.entry.const import ModEntryInfo

    entry = ModEntryInfo(
        name='alas',
        requirements_file='requirements.txt',
        update_pack_deps=('httpx', 'ruamel.yaml'),
        pypi_mirrors='https://mirrors.aliyun.com/pypi/simple',
        update_provide_pypi_mirrors=('https://py38deps.github.io/py38deps/simple/',),
    )

The pack server reads the source from the git blob of a commit, not from a
working tree, and it must not execute code of the repo it packs. This module
provides the parser of the declarations and the reader of one revision:

- PackRepoEntry(source) parses the source of one revision of the entry, its
  declarations are cached properties; the caller reads the blob (the blob of
  a commit, e.g. LookbackWheel);
- parse_mod_entry(source) parses the source itself, with ast: the source is
  never imported, compiled into a callable or evaluated.

The parse is three steps, so a key is added in one place:

1. extract: the parser keeps the value node of the last declaration of every
   key of PARSED_KEYS, from the keyword of the entry construction or from an
   attribute assignment after it;
2. evaluate: every node is evaluated to a Python value, a static expression
   only (see below);
3. validate: the values are put back into an entry dict and handed to
   msgspec against ModEntryInfo, so the model is the single source of truth
   of the accepted types and shapes. The keys the entry omits keep the
   defaults of the model.

The declarations may be the keyword of the entry construction or an attribute
assignment after it:

    entry = ModEntryInfo(name='alas')
    entry.requirements_file = 'requirements.txt'
    entry.update_pack_deps = ('httpx',)
    entry.pypi_mirrors = {'cn': {'self': 'https://pypi.example/simple'}}

The entry may also be one of the default entries of the library, looked up by
key:

    from alasio.config.entry.const import DICT_MOD_ENTRY

    entry = DICT_MOD_ENTRY['alas']
    entry.update_pack_deps = ('httpx',)

The lookup is resolved against the library's own DICT_MOD_ENTRY, never against
the source of the repo: the source is not imported or executed. The values of
the default entry are the starting values, a later attribute assignment
replaces one of them, and a later ModEntryInfo construction resets all of
them to the defaults of the model.

A value must be a static expression, read in the order of the source:

- a literal of a str, a bool, an int or None;
- a list, a tuple or a set literal of static expressions;
- a dict literal of static expressions (its values are kept, a dict declaring
  update_pack_deps declares its keys);
- dict.fromkeys() over one of the forms above, for update_pack_deps;
- a module-level name that was assigned one of the forms above;
- DEFAULT_PYPI_MIRRORS, imported from the library entry module (or through an
  alias of it), the default of ModEntryInfo;
- deepcopy() or .copy() over one of the forms above.

Everything else raises ValueError: a dynamic expression, a mutation of a
parsed key after the declaration (e.g. .update() or an item assignment), an
entry built by a helper call that declares none of the keys, a DICT_MOD_ENTRY
lookup whose name is not imported from the library module, whose key is not a
static str of the library dict, or a declaration outside the module level. A
pack must never silently lose a dependency channel, or fetch from a mirror the
entry did not declare, because this parser could not read the declaration.

Usage:
    from alasio.deploy_dev.pack_server.parse_entry import PackRepoEntry, parse_mod_entry

    entry = parse_mod_entry(source, source_name='module/config/const.py at c3')
    entry.requirements_file, entry.update_pack_deps, entry.pypi_mirrors, \
        entry.update_provide_pypi_mirrors

    entry = PackRepoEntry(source)
    entry.requirements_file, entry.update_pack_deps, entry.pypi_mirrors, \
        entry.update_provide_pypi_mirrors
"""

import ast
from copy import deepcopy

import msgspec

from alasio.config.entry.const import (
    DEFAULT_PYPI_MIRRORS as _LIBRARY_DEFAULT_PYPI_MIRRORS, DICT_MOD_ENTRY as _LIBRARY_MOD_ENTRY, ModEntryInfo
)
from alasio.ext.cache import cached_property

# Path of the mod entry in a repo, the file ModLoader.self_mod reads. It is
# kept here so the caller that reads the git blob and the parser agree on the
# layout of a mod.
ENTRY_FILE = 'module/config/const.py'

# module of the library default entries, the only module a DICT_MOD_ENTRY
# lookup is resolved from
_ENTRY_CONST_MODULE = 'alasio.config.entry.const'

# the declarations the pack server reads from a mod entry: the module level
# keys of the entry. The parser extracts exactly these (step 1), evaluates
# their nodes (step 2), and rebuilds an entry for ModEntryInfo to validate
# (step 3), so the model is the single source of truth of the accepted types.
# A new key is added here and to ModEntryInfo, the parser changes no further.
PARSED_KEYS = (
    'requirements_file',
    'update_pack_deps',
    'pypi_mirrors',
    'update_provide_pypi_mirrors',
)

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


def parse_mod_entry(source, source_name=''):
    """
    Parse the declarations of a mod entry and validate them with the model.

    The source is parsed with ast and never executed. The parse is three
    steps: the value node of every key of PARSED_KEYS is extracted, the nodes
    are evaluated to Python values, and the values are put back into an entry
    dict handed to msgspec against ModEntryInfo. The keys the entry does not
    declare keep the defaults of the model.

    Args:
        source (str | bytes): Python source of the entry module. A bytes
            source is decoded by ast with the encoding declaration it
            carries, like Python itself
        source_name (str): Name of the source used in the error messages,
            e.g. 'module/config/const.py at commit c3'. Defaults to '',
            the message only carries the reason

    Returns:
        ModEntryInfo: The parsed declarations, validated

    Raises:
        ValueError: If the source is not valid Python, a declaration cannot
            be read, or a value does not fit ModEntryInfo
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        message = f'invalid Python at line {e.lineno}: {e.msg}'
        raise ValueError(_with_source_name(source_name, message)) from e

    parser = _EntryParser()
    try:
        parser.visit(tree)
        return parser.entry()
    except _Unsupported as e:
        raise ValueError(_with_source_name(source_name, str(e))) from None
    except msgspec.ValidationError as e:
        raise ValueError(_with_source_name(source_name, f'invalid entry value: {e}')) from None


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
    The declarations of the mod entry of one revision.

    The source is the content of `module/config/const.py` at one commit, read
    by the caller from the git blob, never from a working tree: the pack
    server clones a repo without a working tree. The parse runs once, on the
    first read of a declaration, see mod_entry.

    Usage:
        entry = PackRepoEntry(source)
        entry.requirements_file
        entry.update_pack_deps
        entry.pypi_mirrors
        entry.update_provide_pypi_mirrors
    """

    def __init__(self, source, source_name=''):
        """
        Args:
            source (str | bytes): Python source of the entry module, e.g. the
                blob of module/config/const.py at a commit. A bytes source is
                decoded by ast with the encoding declaration it carries, like
                Python itself
            source_name (str): Name of the source used in the error messages,
                e.g. 'module/config/const.py at commit c3'. Defaults to ''
        """
        self.source = source
        self.source_name = source_name

    @cached_property
    def mod_entry(self):
        """
        The entry of the revision, parsed and validated.

        An entry that omits a key gives the default of ModEntryInfo: the
        runtime entry of the mod declares the same values.

        Returns:
            ModEntryInfo: The parsed entry

        Raises:
            ValueError: If the entry source is not valid Python, or its
                declarations cannot be read
        """
        return parse_mod_entry(self.source, source_name=self.source_name)

    @cached_property
    def requirements_file(self):
        """
        The dependency file of the mod, relative to the root of the repo.

        The file the dependencies of the mod are pinned in, a path with '/',
        e.g. 'requirements.txt'. An entry that declares none gives the default
        of ModEntryInfo.

        Returns:
            str: Path of the dependency file in the repo
        """
        return self.mod_entry.requirements_file

    @cached_property
    def update_pack_deps(self):
        """
        The dependency names the entry updates by pack, in source order.

        Returns:
            tuple[str, ...]: Declared names, empty when the entry declares
                no name
        """
        return self.mod_entry.update_pack_deps

    @cached_property
    def pypi_mirrors(self):
        """
        The PyPI mirrors the distributions of the mod are fetched from.

        Returns:
            str | dict: The declared mirror input, see Mirrors.from_input()
        """
        # a copy: the caller may edit the structure it gets, the cached entry
        # is never shared
        return deepcopy(self.mod_entry.pypi_mirrors)

    @cached_property
    def update_provide_pypi_mirrors(self):
        """
        The PyPI indexes the update packs of the mod provide.

        Returns:
            tuple[str, ...]: The declared index base urls, in source order
        """
        return self.mod_entry.update_provide_pypi_mirrors


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


def _entry_key_of(node):
    """
    Name the parsed key an expression is the attribute of the entry of.

    Args:
        node (ast.AST): Expression to inspect

    Returns:
        str: The key, e.g. 'pypi_mirrors' for entry.pypi_mirrors, '' when the
            expression is not an attribute of the entry or the attribute is
            not one of PARSED_KEYS
    """
    if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == 'entry'
    ):
        return ''
    return node.attr if node.attr in PARSED_KEYS else ''


def _entry_key_root(node):
    """
    Name the parsed key an expression is rooted at.

    The forms are entry.<key>.update, entry.<key>['x'] and any item or
    attribute chain of them, e.g. entry.pypi_mirrors['cn'].pop: the mutations
    the parser refuses, because the value would be read without them.

    Args:
        node (ast.AST): Expression to inspect

    Returns:
        str: The key the expression is rooted at, '' when it is not rooted at
            one of PARSED_KEYS
    """
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        key = _entry_key_of(node)
        if key:
            return key
        node = node.value
    return ''


def _mentions_entry(node):
    """
    Check whether a statement mentions the entry or its declarations.

    It is used on the statements the parser does not read, e.g. a conditional
    block or an import, so a declaration hidden there fails loudly instead of
    silently producing the defaults. A plain use of entry, e.g. setting
    entry.root, does not count: only an assignment to the entry name, a
    PARSED_KEYS keyword or attribute, and an import of entry do.

    Args:
        node (ast.AST): Statement to inspect

    Returns:
        bool: True when the statement may declare the entry
    """
    for child in ast.walk(node):
        if isinstance(child, ast.keyword) and child.arg in PARSED_KEYS:
            return True
        if isinstance(child, ast.Attribute) and child.attr in PARSED_KEYS:
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


def _check_provide_mirrors(urls):
    """
    Check that every provided index is an http(s) base url

    The urls are matched against the sources of a dependency file verbatim
    (after the normalization of the matcher), a url the pack server cannot
    request would silently provide nothing.

    Args:
        urls (Iterable[str]): update_provide_pypi_mirrors of the entry

    Raises:
        _Unsupported: If a url is not an http(s) url
    """
    for url in urls:
        scheme, sep, rest = url.partition('://')
        if not sep or scheme.lower() not in ('http', 'https') or not rest:
            raise _Unsupported(
                f'update_provide_pypi_mirrors must be http(s) urls, got {url!r}')


def _dedup_names(names):
    """
    Drop the repeated names of a declaration, keeping the first occurrence

    Args:
        names (list | tuple | set | Any): The declared names

    Returns:
        The names without a repeat, an input that is not a list, a tuple or a
            set is returned as it is, for the model to refuse it
    """
    if not isinstance(names, (list, tuple, set, frozenset)):
        return names
    out = []
    for name in names:
        if name not in out:
            out.append(name)
    return out


class _EntryParser:
    """
    Walk the module level statements of an entry module.

    Step 1 of the parse: the parser keeps the module level names it can
    evaluate, the local names of the library entry imports, and the value node
    of the last declaration of every key of PARSED_KEYS. Only module level
    assignments are read, see _mentions_entry for the guard of the other
    statements.
    """

    def __init__(self):
        # module level names that hold a static value, {name: value}, the
        # names projection of the value (the keys of a dict)
        self.constants = {}
        # module level names that hold a static value, {name: value}, the
        # value as it is written (the values of a dict)
        self.values = {}
        # local names bound to the library DICT_MOD_ENTRY by an import
        self.dict_mod_entry_names = set()
        # local names bound to the library DEFAULT_PYPI_MIRRORS by an import
        self.default_mirrors_names = set()
        # local names bound to the library entry module by an import
        self.entry_module_names = set()
        # the values of the library entry an entry was copied from with a
        # DICT_MOD_ENTRY lookup, None for a fresh entry
        self.base = None
        # key -> value node of the last declaration of the key, PARSED_KEYS
        # only
        self.nodes = {}

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
            key = _entry_key_of(node.target) or _entry_key_root(node.target)
            if key:
                raise _Unsupported(
                    f'augmented assignment to {key} is not supported', node)
            self._check_other(node)
            return

        if isinstance(node, ast.Delete):
            for target in node.targets:
                key = _entry_key_of(target) or _entry_key_root(target)
                if key:
                    raise _Unsupported(f'deleting {key} is not supported', node)
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
                'entry and its declarations must be declared by module level assignments',
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
                    elif alias.name == 'DEFAULT_PYPI_MIRRORS':
                        self.default_mirrors_names.add(alias.asname or alias.name)
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

    def _is_default_mirrors(self, node):
        """
        Check whether an expression is the library DEFAULT_PYPI_MIRRORS.

        The accepted forms are the name imported from the library entry
        module, e.g. DEFAULT_PYPI_MIRRORS or an alias of it, and the attribute
        of a module imported as an alias, e.g. const.DEFAULT_PYPI_MIRRORS.

        Args:
            node (ast.AST): Expression to inspect

        Returns:
            bool: True when the expression is the library DEFAULT_PYPI_MIRRORS
        """
        if isinstance(node, ast.Name):
            return node.id in self.default_mirrors_names
        if isinstance(node, ast.Attribute):
            return (
                node.attr == 'DEFAULT_PYPI_MIRRORS'
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
        return self._eval_value(node)

    def _assign_dict_mod_entry(self, value):
        """
        Read an entry assigned from the library DICT_MOD_ENTRY.

        The lookup is resolved by the key in the library dict, never by the
        source of the repo: the entry module is not imported or executed. The
        key must be a static str, and the values of the library entry are the
        base of the parsed keys.

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
        self.base = {name: deepcopy(getattr(entry, name)) for name in PARSED_KEYS}
        self.nodes = {}

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

        key = _entry_key_of(target)
        if key:
            # step 1: keep the node, step 2 evaluates it once the whole source
            # is read
            self.nodes[key] = value
            return

        key = _entry_key_root(target)
        if key:
            raise _Unsupported(
                f'assignment to an item or an attribute of {key} is not supported', target)

        # other attributes and items, e.g. entry.root or a plain dict item,
        # do not declare a parsed key

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
        keywords = {keyword.arg: keyword.value for keyword in value.keywords}
        declared = {key: keywords[key] for key in PARSED_KEYS if key in keywords}
        if not declared and not _is_mod_entry_info(value.func):
            # a helper may set a declaration in its own body, where this
            # parser cannot see it
            raise _Unsupported(
                'entry is constructed by a call that does not declare '
                + ', '.join(PARSED_KEYS), value)
        # a new construction starts from the defaults of the model
        self.base = None
        self.nodes = declared

    def _record_constant(self, name, value):
        """
        Record a module level name for a later reference.

        A value the parser cannot evaluate is recorded as unknown: the name is
        only an error when a declaration actually references it, so an
        unrelated dynamic module level value does not fail the parse. The name
        is recorded twice, the two projections of a dict: its keys for
        update_pack_deps, its values for the mirror declarations.

        Args:
            name (str): Name of the assignment target
            value (ast.AST): Value of the assignment
        """
        try:
            self.constants[name] = self._eval_raw(value)
        except _Unsupported:
            self.constants[name] = _UNKNOWN
        try:
            self.values[name] = self._eval_value(value)
        except _Unsupported:
            self.values[name] = _UNKNOWN

    def entry(self):
        """
        Rebuild the entry from the parsed values and validate it.

        Step 2 evaluates the node of every declared key, step 3 puts the
        values into an entry dict and hands it to msgspec against
        ModEntryInfo: the types, the shapes of the values and the defaults of
        the keys the entry omits are the model's business, never the parser's.

        Returns:
            ModEntryInfo: The parsed entry

        Raises:
            _Unsupported: If a value is not a static expression, or a provided
                index is not an http(s) url
            msgspec.ValidationError: If a value does not fit ModEntryInfo
        """
        data = {} if self.base is None else self.base
        for key, node in self.nodes.items():
            try:
                if key == 'update_pack_deps':
                    # a dict declares its keys, the names are deduplicated in
                    # the order of the source
                    data[key] = _dedup_names(self._eval_names(node))
                elif key == 'update_provide_pypi_mirrors':
                    # the urls are deduplicated too, a set has no order of its
                    # own and a repeated index is provided once
                    data[key] = _dedup_names(self._eval_value(node))
                else:
                    data[key] = self._eval_value(node)
            except _Unsupported as e:
                raise _Unsupported(f'{key}: {e}') from None
        # the name is not a parsed key: the entry only has to validate, the
        # pack server reads the declarations and never the name
        data.setdefault('name', '')
        entry = msgspec.convert(data, type=ModEntryInfo, strict=False)
        _check_provide_mirrors(entry.update_provide_pypi_mirrors)
        return entry

    def _eval_names(self, node):
        """
        Evaluate a declaration to the names it declares.

        A dict declares its keys, the other collections declare their items,
        dict.fromkeys() projects its first argument: the names projection of a
        module level name is recorded by _record_constant, so the values of a
        dict constant are never evaluated. The names are read here, the type
        (a str) and the shape (a tuple of str) are checked by the model in
        step 3.

        Args:
            node (ast.AST): Expression to evaluate

        Returns:
            list | tuple | set: The declared names, a declaration of None
                declares no name

        Raises:
            _Unsupported: If the expression is not static
        """
        value = self._eval_raw(node)
        if value is None:
            return []
        if isinstance(value, dict):
            return list(value)
        return value

    def _eval_raw(self, node):
        """
        Evaluate a static expression of the module, dicts projected to keys.

        Args:
            node (ast.AST): Expression to evaluate

        Returns:
            The static value of the expression, the keys of a dict

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
                keys.append(self._eval_raw(key))
            try:
                return dict.fromkeys(keys, None)
            except TypeError as e:
                raise _Unsupported(f'dict key is not hashable: {e}', node) from None

        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [self._eval_raw(element) for element in node.elts]

        if isinstance(node, ast.Call):
            if not _is_dict_fromkeys(node.func):
                raise _Unsupported(
                    'unsupported call, only dict.fromkeys() is read', node)
            if not 1 <= len(node.args) <= 2 or node.keywords:
                raise _Unsupported(
                    'dict.fromkeys() takes one or two positional arguments', node)
            items = self._eval_raw(node.args[0])
            if not isinstance(items, (dict, list, tuple, set, frozenset)):
                raise _Unsupported(
                    'dict.fromkeys() requires a dict, list, tuple or set of names', node)
            return dict.fromkeys(items, None)

        raise _Unsupported(f'unsupported expression: {type(node).__name__}', node)

    def _eval_value(self, node):
        """
        Evaluate a static expression of the module, keeping the values.

        The dict literal is read as it is written, nested dicts and their
        values included, and the copy idioms of a default structure are
        accepted; the update_pack_deps projection of a dict is _eval_names(),
        which reads the keys only.

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
            if self._is_default_mirrors(node):
                return deepcopy(_LIBRARY_DEFAULT_PYPI_MIRRORS)
            try:
                value = self.values[node.id]
            except KeyError:
                raise _Unsupported(f'unknown name "{node.id}"', node) from None
            if value is _UNKNOWN:
                raise _Unsupported(f'name "{node.id}" is not a static value', node)
            return value

        if isinstance(node, ast.Attribute):
            if self._is_default_mirrors(node):
                return deepcopy(_LIBRARY_DEFAULT_PYPI_MIRRORS)
            raise _Unsupported(f'unsupported expression: {type(node).__name__}', node)

        if isinstance(node, ast.Dict):
            out = {}
            for key, value in zip(node.keys, node.values):
                if key is None:
                    raise _Unsupported('dict unpacking is not supported', node)
                out[self._eval_value(key)] = self._eval_value(value)
            return out

        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [self._eval_value(element) for element in node.elts]

        if isinstance(node, ast.Call):
            # deepcopy(DEFAULT_PYPI_MIRRORS) and DEFAULT_PYPI_MIRRORS.copy(),
            # the copy idioms of a default structure
            if _is_copy_call(node):
                return deepcopy(self._eval_value(node.args[0] if node.args else node.func.value))
            raise _Unsupported(
                'unsupported call, only deepcopy() and .copy() calls are read', node)

        raise _Unsupported(f'unsupported expression: {type(node).__name__}', node)


def _is_copy_call(node):
    """
    Check whether a call is a copy of its argument or of its attribute owner

    The accepted forms are deepcopy(x), copy.deepcopy(x) with one positional
    argument and no keyword, and x.copy() with no argument.

    Args:
        node (ast.Call): Call to inspect

    Returns:
        bool: True for one of the copy forms
    """
    func = node.func
    if isinstance(func, ast.Name) and func.id == 'deepcopy':
        return len(node.args) == 1 and not node.keywords
    if isinstance(func, ast.Attribute) and func.attr == 'deepcopy':
        return len(node.args) == 1 and not node.keywords and isinstance(func.value, ast.Name)
    if isinstance(func, ast.Attribute) and func.attr == 'copy':
        return not node.args and not node.keywords
    return False
