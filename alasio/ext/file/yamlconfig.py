"""
Poor yaml read/write module that preserves comments.

Unlike :mod:`alasio.ext.file.yamlfile`, this module validates parsed yaml
with msgspec structs, and writes comments back into the yaml file.

Comments are defined on the validation model with msgspec annotations::

    class Config(msgspec.Struct):
        port: Annotated[int, Meta(extra={"help": "Port to listen on"})] = 8080

The yaml text is generated with pyyaml, then comment lines are inserted above
the line of each key that has help text: ::

    # Port to listen on
    port: 8080

The comments are placed in two steps, so no type has to be guessed from the
text. :func:`build_help_map` reads the model and builds a flat map of every
node it declares, keyed by its path: ::

    {('Repo',): None,
     ('Repo', 'Remote'): 'Remote url',
     ('Publish',): 'the remotes',
     ('Publish', ANY_KEY): None,
     ('Publish', ANY_KEY, 'Bucket'): 'Bucket name'}

The value is the help text of the node, or None when the model defines the
node without a help. ``ANY_KEY`` is the path segment of a level the model does
not name: the value of a map the operator keys, and the element of a sequence.
A step of a path the map does not name is looked up through that segment, which
is how a key of a map is told from a field of the model.

:func:`iter_yaml_rows` then yields the key path of every line of the text, with
``ANY_KEY`` for the rows of a sequence element, and
:func:`insert_comments_iter` writes the comment of a path above its line::

    text = insert_comments(text, build_help_map(Model))

Nested structures and multiline strings are supported, pyyaml handles the
parsing and dumping.
"""

import re
from collections import deque
from typing import Generic, List, Type, TypeVar, Union

import msgspec
import yaml
from msgspec import NODEFAULT, Struct
from msgspec.inspect import (
    DictType, FrozenSetType, ListType, Metadata, SetType, StructType, TupleType, UnionType, VarTupleType, type_info
)
from msgspec.msgpack import encode as msgpack_encode
from msgspecerror import ErrorInfo, load_msgpack_with_default

from alasio.config_dev.format.format_i18n import format_i18n
from alasio.ext.cache import cached_property
from alasio.ext.cache.msgspec_meta import get_field_metadata
from alasio.ext.deep import deep_iter_diff, deep_set_with_error
from alasio.ext.file.yamlfile import yaml_dumps, yaml_loads
from alasio.ext.path.atomic import atomic_read_bytes, atomic_read_text, atomic_write
from alasio.logger import logger

T_model = TypeVar('T_model', bound=Struct)

# the inspect types of a node that holds its items without keys of their own
SEQUENCE_TYPES = (ListType, SetType, FrozenSetType, VarTupleType)

# A line of a yaml mapping, 'key: value' or 'key:'. The colon ends the key only
# when a space or the end of the line follows it, so a scalar like a url
# ('https://host/path') is not read as a key
REGEX_KEY = re.compile(r'^([^:]+):(?=\s|$)')
# The value of a key that opens a block scalar, e.g. '|-', '>' or '| # note'.
# The explicit indent indicator may sit on either side of the chomping one:
# '|2-', '|-2'
REGEX_MULTILINE_STRING = re.compile(r'^[|>][+-]?\d?[+-]?(?:\s+#.*)?$')
# A line of a yaml sequence, '- value' or a lone '-'. The dashes of a nested
# sequence are matched one level at a time, see iter_yaml_rows
REGEX_SEQUENCE_ITEM = re.compile(r'^(\s*)-(?:[ \t]+(.*))?$')


class HelpKey:
    """
    A path segment of the help map that is not a name of the yaml document

    A node of a config does not always have a name: the value of a map is keyed
    by the operator, the element of a sequence is keyed by nothing at all. Such
    a level is stored under ``ANY_KEY``. It is an object, so a key of the
    document can never collide with it.
    """

    __slots__ = ('_name',)

    def __init__(self, name):
        """
        Args:
            name (str): Name of the key, used by its repr
        """
        self._name = name

    def __repr__(self):
        return self._name


# The path segment of a level whose keys the model does not name: the value of
# a map the operator keys, and the element of a sequence
ANY_KEY = HelpKey('ANY_KEY')


def build_help_map(model):
    """
    Build the flat help map of a model

    The map is read from the model, not from the data: a node of the document
    exists because the model declares it, so a list of a thousand elements
    documents its element type once, and the cost of the map follows the
    number of types the model declares, not the number of values the file
    holds.

    A key of the map is the tuple path of a node of the yaml document, and its
    value is the help text of the node, or None when the model declares the
    node without a help. A level the model does not name -- the value of a map
    the operator keys, the element of a sequence -- is the path segment
    ``ANY_KEY``: ::

        Repo:
          # Remote url
          Remote: ...

        Publish:
          cos-main:
            # Bucket name
            Bucket: ...

        {
            ('Repo',): None,
            ('Repo', 'Remote'): 'Remote url',
            ('Publish',): 'the remotes',
            ('Publish', ANY_KEY): None,
            ('Publish', ANY_KEY, 'Bucket'): 'Bucket name',
        }

    The types are read with :func:`msgspec.inspect.type_info`, the inspection
    api of msgspec, and the help texts with the adapters of msgspecerror, so
    neither side of the read depends on the shape msgspec happens to keep.

    Args:
        model (type | Struct): Class of a msgspec.Struct, or an instance of
            one, whose class is the model

    Returns:
        dict: {path: help_text | None}
    """
    if not (isinstance(model, type) and issubclass(model, Struct)):
        model = type(model)
    out = {}
    # the set guards a model that declares itself, e.g. a tree of its own nodes
    _collect_help(out, type_info(model), (), frozenset())
    return out


def _collect_help(out, node, path, seen):
    """
    Collect every node a type declares below a path

    Args:
        out (dict): {path: help_text | None} map to fill
        node (msgspec.inspect.Type): Type of the node, from ``type_info``
        path (tuple): Path of the node
        seen (frozenset): Classes of the structs the path went through, so a
            struct that holds one of itself ends the walk instead of looping
    """
    kind = type(node)
    while kind is Metadata:
        # ``Annotated[T, Meta(...)]`` is inspected as the type it wraps plus the
        # metadata, the help of the node is read from the field of its struct
        node = node.type
        kind = type(node)
    if kind is StructType:
        if node.array_like:
            # the document holds the values of such a struct without keys
            return
        if node.cls in seen:
            return
        seen = seen | {node.cls}
        if node.tag_field and node.tag is not None:
            # the tag is a key msgspec writes, no field of the model declares it
            out[path + (node.tag_field,)] = f'{node.tag_field} selects the variant of this entry'
        metadata = get_field_metadata(node.cls)
        for field in node.fields:
            child = path + (field.encode_name,)
            meta = metadata.get(field.name)
            help_text = meta.extra.get('help') if meta else None
            out[child] = format_i18n(help_text) if help_text else None
            _collect_help(out, field.type, child, seen)
    elif kind is DictType:
        # the keys of a map are chosen by the operator, the model names none
        _collect_unnamed_help(out, [node.value_type], path, seen)
    elif kind is TupleType:
        _collect_unnamed_help(out, node.item_types, path, seen)
    elif kind in SEQUENCE_TYPES:
        # every other sequence holds items of one type: list, set, frozenset,
        # and the variable length tuple, whose item type is ``item_type``
        _collect_unnamed_help(out, [node.item_type], path, seen)
    elif kind is UnionType:
        # the file holds one of the variants, they document the same path
        for variant in node.types:
            _collect_help(out, variant, path, seen)


def _collect_unnamed_help(out, item_types, path, seen):
    """
    Collect the nodes of a level whose keys the model does not name

    Args:
        out (dict): {path: help_text | None} map to fill
        item_types (Iterable): Types of the items of the level
        path (tuple): Path of the container
        seen (frozenset): Classes of the structs the path went through
    """
    any_path = path + (ANY_KEY,)
    # the level itself has no help, it is keyed by the operator
    out.setdefault(any_path, None)
    for item_type in item_types:
        _collect_help(out, item_type, any_path, seen)


def split_yaml_key(text):
    """
    Split a yaml line into the key of its mapping and the rest of the line

    Args:
        text (str): Line of yaml text, its indentation removed

    Returns:
        tuple: (key, rest), key is '' when the line holds no mapping key,
            e.g. a scalar or a block scalar
    """
    result = REGEX_KEY.match(text)
    if not result:
        return '', ''
    return result.group(1).strip(), text[result.end():].strip()


def iter_yaml_rows(text):
    """
    Iter the rows of a yaml text, yield (path, row) for each line

    path is the tuple of keys of the line, e.g. ('port',), ('inner', 'port').
    The element of a sequence has no name, so its level of the path is
    ``ANY_KEY``: the line ``- Name: a`` under ``Items`` is the path
    ('Items', ANY_KEY, 'Name').

    The text says where the rows are, not what they mean: a config that names
    a key the model does not know is read as any other key.

    Args:
        text (str): yaml text

    Yields:
        tuple: (path, row), path is None for a line that holds no key of a
            mapping, e.g. a scalar element of a sequence, the content of a
            block scalar, a comment or an empty line. row is the line as it is
    """
    # Stack of (column, key) of the open mappings and sequences
    stack: "deque[tuple[int, object]]" = deque()
    # Column of the block scalar the walk is inside, None when outside one
    block_column = None
    for line in text.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if not stripped:
            # Empty line, doesn't close a mapping or a block scalar
            yield None, line
            continue
        if block_column is not None:
            if indent > block_column:
                # Content of the block scalar
                yield None, line
                continue
            block_column = None
        if stripped.startswith('#'):
            # Comment of the text, it is replaced by the ones of the model
            yield None, line
            continue
        # A sequence item is an unnamed level, possibly nested, e.g.
        # '- - Name: a'. The column of the content moves past every dash, so
        # the keys of an element are read at their own column
        column = indent
        content = stripped
        item_column = indent
        while True:
            item = REGEX_SEQUENCE_ITEM.match(content)
            if not item:
                break
            # A sequence may be indented under its key: the key at that column
            # is its parent, while a marker at that column is the element that
            # came before this one
            while stack and column < stack[-1][0]:
                stack.pop()
            while stack and column == stack[-1][0] and stack[-1][1] is ANY_KEY:
                stack.pop()
            stack.append((column, ANY_KEY))
            item_column = column
            content = item.group(2) or ''
            column += len(item.group(0)) - len(content)
        key, rest = split_yaml_key(content)
        if not key:
            # A scalar element, e.g. '- master', or a block scalar of an
            # element. The content of the block scalar is indented past the
            # dash, not past the column of its indicator
            if REGEX_MULTILINE_STRING.match(content):
                block_column = item_column
            yield None, line
            continue
        while stack and column <= stack[-1][0]:
            stack.pop()
        path = tuple([k for _, k in stack] + [key])
        stack.append((column, key))
        if REGEX_MULTILINE_STRING.match(rest):
            block_column = column
        yield path, line


def resolve_help(help_map, path):
    """
    The help text of a key path of the yaml text

    The whole path is looked up first, and only a path the map does not hold
    is walked step by step: each step is looked up as it is, then as
    ``ANY_KEY``, because a key the map does not name is chosen by the operator
    or is the element of a sequence, and belongs to the unnamed level of its
    parent. A step neither way names is not part of the model, and the path has
    no help.

    Args:
        help_map (dict): Map built by ``build_help_map``
        path (tuple): Key path of a line, from ``iter_yaml_rows``

    Returns:
        str | list[str] | None: Help text of the node, None when it has none
            or the path is not in the map
    """
    try:
        return help_map[path]
    except KeyError:
        pass
    node = ()
    for key in path:
        candidate = node + (key,)
        if candidate not in help_map:
            candidate = node + (ANY_KEY,)
            if candidate not in help_map:
                return None
        node = candidate
    return help_map.get(node)


def insert_comments_iter(rows, help_map):
    """
    Iterate (path, row) pairs, yield the comment lines of keys that have
    help text before the row

    Args:
        rows (Iterable): (path, row) pairs from ``iter_yaml_rows()``
        help_map (dict): Map built by ``build_help_map``

    Yields:
        str: Comment lines of the rows that have help text, and the rows
    """
    for path, row in rows:
        if path:
            help_text = resolve_help(help_map, path)
            if help_text is not None:
                # format_i18n returns a list of lines for multiline help, str for single line
                comments = [help_text] if isinstance(help_text, str) else help_text
                indent = row[:len(row) - len(row.lstrip())]
                for comment in comments:
                    yield f'{indent}# {comment}'
        yield row


def insert_comments(text, help_map):
    """
    Insert the comment lines of the help map above the lines of the yaml text

    Args:
        text (str): yaml text
        help_map (dict): Map built by ``build_help_map`` from the data the
            text was dumped from

    Returns:
        str: yaml text with the comments inserted
    """
    if not text or not help_map:
        return text
    lines = list(insert_comments_iter(iter_yaml_rows(text), help_map))
    commented = '\n'.join(lines)
    # the dumped text ends with a newline, the commented text ends like it
    if text.endswith('\n'):
        return commented + '\n'
    return commented


class YamlConfig(Generic[T_model]):
    """
    Yaml config reader/writer with msgspec validation and comment preservation.

    Comments are defined on the validation model fields with
    ``Annotated[str, Meta(extra={"help": "xxx"})]``, and written into the
    yaml file above the line of the key of the field.

    Attributes:
        file (str): YAML file path
        model (type[T_model]): Subclass of msgspec.Struct
        data (T_model): Validated data, read from the file on the first access
            and cached, use ``self.data.attr`` to access fields. A file that is
            missing or cannot be read falls back to the model defaults and is
            written back with the help comments, so the user has a file to fix
        errors (list[ErrorInfo | Exception]): Errors or exceptions of the last
            read or validate, empty before the data is read and if none.
            Exceptions are read failures such as FileNotFoundError,
            UnicodeDecodeError and yaml.YAMLError

    Args:
        file (str): YAML file path
        model (type[T_model]): Subclass of msgspec.Struct to validate config data.
            All fields must have default values, so the model can be default constructed
    """

    def __init__(self, file, model: Type[T_model]):
        self.file = file
        self.model: Type[T_model] = model
        self.errors: "List[Union[ErrorInfo, Exception]]" = []
        # Model must be a msgspec struct
        if not isinstance(model, type) or not issubclass(model, Struct):
            raise TypeError(f'Model must be a subclass of msgspec.Struct, got {model!r}')
        # Model must be default constructible, all fields need default values
        try:
            model()
        except Exception as e:
            raise ValueError(
                f'Model {model.__name__} must be default constructible, '
                f'all fields need default values: {e}'
            ) from e

    @cached_property
    def data(self) -> T_model:
        """
        The validated data of the file, read on the first access and cached

        The read is lazy so a config that is never used does not touch the
        disk, and write(template=True) does not read the file at all. Fields
        that fail the validation fall back to their defaults. A file that
        does not exist or cannot be read falls back to the model defaults
        too, the read errors are recorded in ``errors``, and the file is
        written back with the read data and the help comments, so the user
        sees the file and can fix it.

        Returns:
            T_model: Validated data
        """
        try:
            text = atomic_read_bytes(self.file)
        except (FileNotFoundError, UnicodeDecodeError) as e:
            # File not found or not utf-8 encoded, use defaults
            self.errors = [e]
            self._log_errors(self.errors)
            data = self.model()
        else:
            try:
                content = yaml_loads(text)
            except yaml.YAMLError as e:
                # Invalid yaml, use defaults
                self.errors = [e]
                self._log_errors(self.errors)
                data = self.model()
            else:
                if content is None:
                    # Empty content or only comments, use defaults
                    content = {}
                data, errors = load_msgpack_with_default(msgpack_encode(content), self.model)
                if data is NODEFAULT:
                    # Shouldn't happen, model is checked default constructible in __init__
                    data = self.model()
                self.errors = errors
                if errors:
                    self._log_errors(errors)

        if self.errors:
            # Auto fix: write the file back with the read data (the model
            # defaults when the file is missing or cannot be read), the
            # errors stay in self.errors for the caller to look at
            self._write(data)
        return data

    @staticmethod
    def _log_errors(errors):
        """
        Log each error or exception as a warning

        Args:
            errors (list[ErrorInfo | Exception]): Errors to log
        """
        for error in errors:
            logger.warning(f'Invalid deploy config value: {error}')

    def validate(self):
        """
        Validate current self.data with the model, reusing the same validation
        logic as the data property, fields that fail validation fall back to
        defaults

        Returns:
            bool: True if self.data is valid, False if errors were found
        """
        obj, errors = load_msgpack_with_default(msgpack_encode(self.data), self.model)
        if obj is NODEFAULT:
            # Shouldn't happen, model is checked default constructible in __init__
            obj = self.model()
        cached_property.set(self, 'data', obj)
        self.errors = errors
        if errors:
            self._log_errors(errors)
            return False
        return True

    def set(self, key, value):
        """
        Set value at the key path of data with model validation.

        The current data is converted to a builtin dict with
        ``msgspec.to_builtins``, value is set at the key path with
        ``deep_set_with_error``, then the dict is validated back into the
        model with ``msgspec.convert``. On success the cached data is replaced
        with the validated model and True is returned, on failure the
        exception is recorded in self.errors and logged, and the cached data
        is left unchanged.

        Args:
            key (tuple[str]): Key path in the yaml structure, keys are encode
                names of model fields, e.g. ('port',), ('inner', 'port')
            value: Value to set at the key path, must be valid for the type
                of the field at the path

        Returns:
            bool: True if the value was set, False if the key path doesn't
                exist or validation failed
        """
        if not key:
            error = ValueError(f'Key path must not be empty, got {key!r}')
            self.errors = [error]
            self._log_errors(self.errors)
            return False
        data = msgspec.to_builtins(self.data)
        try:
            deep_set_with_error(data, key, value)
        except (KeyError, TypeError, IndexError):
            error = KeyError(f'Key path {key!r} does not exist in data')
            self.errors = [error]
            self._log_errors(self.errors)
            return False
        try:
            obj = msgspec.convert(data, self.model)
        except Exception as e:
            self.errors = [e]
            self._log_errors(self.errors)
            return False
        cached_property.set(self, 'data', obj)
        self.errors = []
        return True

    def write(self, template=False, skip_same=True):
        """
        Write the yaml file, comments are inserted above the line of each key
        from ``Meta(extra={"help": ...})`` annotations

        Args:
            template (bool): True to build the content from the model
                directly, the file is not read: the written file is the
                default config of the model, whatever the file holds on the
                disk. The file is still compared by skip_same. Defaults to
                False, the current data is written, see data
            skip_same (bool): True to skip writing if existing content is the same
                as content to write. This would reduce disk write but add disk read

        Returns:
            bool: if write
        """
        data = self.model() if template else self.data
        return self._write(data, skip_same=skip_same)

    def write_data(self, data, skip_same=True):
        """
        Write the given data into the yaml file, with the comments of the
        model

        Unlike write(), the file is not read first: a caller that owns the
        data (a generator that builds it from the object of the file) writes
        exactly what it has, the model still supplies the comments and the
        format.

        Args:
            data (T_model): Data to write, a model instance of this config
            skip_same (bool): True to skip writing if existing content is the
                same as content to write. This would reduce disk write but add
                disk read

        Returns:
            bool: if write
        """
        return self._write(data, skip_same=skip_same)

    def _write(self, data, skip_same=True):
        """
        Write data into the yaml file, comments are inserted above the line of
        each key from the help annotations of the model

        Args:
            data (T_model): Data to write
            skip_same (bool): True to skip writing if existing content is the same
                as content to write

        Returns:
            bool: if write
        """
        # yaml_dumps from yamlfile returns utf-8 bytes, decode into str
        text = yaml_dumps(msgspec.to_builtins(data)).decode('utf-8')
        # the help map describes the model, so the cost of it follows the model
        # and not the size of the file, a list documents its element type once
        text = insert_comments(text, build_help_map(self.model))
        if skip_same:
            try:
                old = atomic_read_text(self.file)
            except (FileNotFoundError, UnicodeDecodeError):
                old = None
            if text == old:
                return False

        logger.info(f'Write config {self.file}')
        atomic_write(self.file, text)
        return True

    def _secrete_value(self, path):
        """
        Args:
            path (tuple[str]):

        Returns:
            Any: Any value to override value displayed in show()
                NODEFAULT to display current value
        """
        return msgspec.NODEFAULT

    def show(self):
        """
        Log all settings that are different from the default values, like::

            Showing deploy config of xxx
              Webapp.Lang = 'zh-CN'
            (rest of the config is the same as default)

        Key paths are joined with dots, e.g. ``Webapp.Lang``. When no setting
        differs from the default, ``(config is the same as default)`` is
        logged instead of the rest line.
        """
        logger.info(f'Showing deploy config of {self.file}')
        default = msgspec.to_builtins(self.model())
        data = msgspec.to_builtins(self.data)
        count = 0
        for path, _, after in deep_iter_diff(default, data):
            secret = self._secrete_value(path)
            if secret is not msgspec.NODEFAULT:
                after = secret
            logger.info(f'  {".".join(path)} = {after!r}')
            count += 1
        if count:
            logger.info('(rest of the config is the same as default)')
        else:
            logger.info('(config is the same as default)')
