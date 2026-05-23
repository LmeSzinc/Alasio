import typing as t

from alasio.ext.cache import cached_property

if t.TYPE_CHECKING:
    from .gen_base import CodeGenBase
    from .obj_class import Item, Var


class CodeDefinitionError(Exception):
    pass


class ReprWrapper:
    """
    Wrap a string so that ``repr(ReprWrapper(value))`` returns *value* verbatim
    instead of a quoted string.

    Use this to inject bare variable/expression references into ``Item``,
    ``Var``, or any other code-gen call that calls ``repr()`` on its arguments.

    Examples::

        # Before:  gen.Item('my_var')   →  'my_var',
        # After:
        gen.Item(ReprWrapper('my_var'))  →  my_var,

        gen.Var('x', ReprWrapper('some_ref'))  →  x=some_ref,
    """

    def __init__(self, value: str):
        self.value = value

    def __repr__(self):
        return self.value


class ApplyContextName:
    def __init__(self, gen: "CodeGenBase", context_name: str):
        self.gen = gen
        self.context_name = context_name

    def __enter__(self):
        # store context
        self.context_name_prev = self.gen.context_name
        # enter context
        self.gen.context_name = self.context_name

    def __exit__(self, exc_type, exc_val, exc_tb):
        # restore context
        self.gen.context_name = self.context_name_prev


class CodeObject:
    """
    Base class of all objects
    """

    def __init__(self, gen: "CodeGenBase"):
        self.gen = gen
        self._indent = gen.indent
        self._context_name = gen.context_name
        self._anno = ''
        # Capture custom line_ending from enclosing context (e.g. CustomTab)
        ctx = gen.context
        if ctx is not self:
            self._custom_line_ending = getattr(ctx, '_custom_line_ending', '')
        else:
            self._custom_line_ending = ''

        self.context_name = self.__class__.__name__
        self._indent_tab = 1
        self._wrap: "int | str" = 'inline'
        # True when wrap() was explicitly called; __enter__ won't auto-override
        self._wrap_explicit = False

    def wrap(self, wrap: "int | str" = 'auto'):
        """
        Wrap items in collection
        120 (int): wrap at given width
        'inline': no wrap, all items on one line
        'auto': wrap at 120 if exceeds line width, inline otherwise
        'newline': each item on its own line
        'expand': brackets on separate lines, items wrapped inline
        'always': legacy alias for 'newline' (deprecated)
        """
        self._wrap_explicit = True
        if wrap == 'auto':
            wrap = True  # internal: True means auto at 120
        elif wrap == 'inline':
            wrap = 'inline'
        elif wrap in ('newline', 'always'):
            wrap = 'newline'
        elif wrap == 'expand':
            wrap = 'expand'
        self._wrap = wrap
        return self

    def __enter__(self):
        # When used as a context manager, default to newline unless explicitly set
        if not self._wrap_explicit and self._wrap == 'inline':
            self._wrap = 'newline'
        # store indent and context
        self.indent_prev = self.gen.indent
        self.context_name_prev = self.gen.context_name
        self.context_prev = self.gen.context
        # enter indent and context
        self.gen.indent = self.indent_prev + self._indent_tab
        self.gen.context = self
        self.gen.context_name = self.context_name
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # restore
        self.gen.indent = self.indent_prev
        self.gen.context = self.context_prev
        self.gen.context_name = self.context_name_prev

    def apply_context_name(self, context_name: str):
        """
        Temporarily enter a sub context name
        """
        return ApplyContextName(self.gen, context_name)

    def Anno(self, text: str):
        self._anno = f': {text}'
        return self

    def Var(self, value: t.Any):
        """
        Define a value for this object (Var or Anno)
        """
        if self.gen.context_name in ['ClassInherit', 'FuncArgs']:
            self.value = value
        else:
            self.value = repr(value)
        return self

    @cached_property
    def indent_str(self) -> str:
        return "    " * self._indent

    @cached_property
    def line_ending(self) -> str:
        # Use custom line_ending captured from enclosing context (e.g. CustomTab)
        if self._custom_line_ending:
            return self._custom_line_ending
        if self._context_name in ['Dict', 'List', 'Tuple', 'Set', 'Literal', 'ClassInherit', 'FuncArgs', 'Object']:
            return ','
        return ''

    @cached_property
    def between_kv(self):
        if self._context_name in ['Dict']:
            return ': '
        if self._context_name in ['FuncArgs', 'ClassInherit', 'Object']:
            if self._anno:
                return ' = '
            else:
                return '='
        return ' = '

    def generate(self):
        raise NotImplementedError


class GatherItems:
    """
    Gather Item/Var and convert to str.
    Each item's item_str already carries its own line_ending.
    Tokens are joined with a single space.
    """

    def __init__(self, max_width: "bool | int | str" = False):
        self.items: "list[Item | Var]" = []
        # Normalise string aliases
        if max_width == 'auto':
            max_width = True
        elif max_width == 'inline':
            max_width = False
        elif max_width in ('newline', 'always'):
            max_width = 'newline'
        elif max_width == 'expand':
            max_width = True
        self.max_width = max_width

    def add(self, items: "t.Iterable[Item | Var | Anno] | Item | Var | Anno"):
        if isinstance(items, (list, tuple, set)):
            for item in items:
                self.items.append(item)
            return self
        # Item | Var | Anno
        try:
            name = items.__class__.__name__
            if name in ['Item', 'Var', 'Anno']:
                self.items.append(items)  # type: ignore
                return self
        except AttributeError:
            pass
        # any other iterable
        for item in items:  # type: ignore
            self.items.append(item)
        return self

    def get_inline(self):
        items = [i for i in self.items if hasattr(i, 'item_str')]
        if not items:
            return ''
        result = ' '.join(item.item_str for item in items)
        # Remove trailing comma for inline generation;
        # multi-line output (iter_multiline with max_width) keeps per-row commas.
        if result.endswith(','):
            result = result[:-1]
        return result

    def iter_multiline(self):
        """
        Generate compact lines with max width like:
        {indent_str}item1, item2, item3, item4, item5,
        {indent_str}item6, item7, item8, item9, item10,
        {indent_str}item11, item12,

        Each item_str carries its own line_ending.
        Tokens are joined with a single space.

        Yields:
            str:
        """
        if not self.max_width:
            yield self.get_inline()
            return
        if not self.items:
            return
        max_width = self.max_width
        if max_width is True:
            max_width = 120
        elif max_width == 'newline':
            max_width = 1  # force each item to its own line, used via GatherItems('newline')
        buffer = []
        # Filter to items that have item_str (skip Comment, Empty, etc.)
        gather_items = [i for i in self.items if hasattr(i, 'item_str')]
        if not gather_items:
            return
        indent_str = gather_items[0].indent_str
        indent_width = len(indent_str)
        remain_width = max_width - indent_width
        for item in gather_items:
            token = item.item_str
            if buffer:
                # +1 for the space between tokens
                add_length = len(token) + 1
                if add_length <= remain_width:
                    buffer.append(token)
                    remain_width -= add_length
                else:
                    yield ' '.join(buffer)
                    buffer = [token]
                    remain_width = max_width - indent_width - len(token)
            else:
                buffer = [token]
                remain_width = max_width - indent_width - len(token)
            if remain_width <= 0:
                yield ' '.join(buffer)
                buffer = []
                remain_width = max_width - indent_width

        if buffer:
            yield ' '.join(buffer)
