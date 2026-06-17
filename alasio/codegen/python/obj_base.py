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
        # store indent and context at object init
        self._indent_prev = gen.indent
        self._context_name_prev = gen.context_name
        self._context_prev = gen.context
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
        'auto': decide between inline and expand based on GatherItems.DEFAULT_WIDTH
        'newline': each item on its own line
        'expand': brackets on separate lines, items wrapped inline
        'always': legacy alias for 'newline' (deprecated)
        """
        self._wrap_explicit = True
        if wrap in ('newline', 'always'):
            wrap = 'newline'
        self._wrap = wrap
        return self

    def __enter__(self):
        # When used as a context manager, default to newline unless explicitly set
        if not self._wrap_explicit and self._wrap == 'inline':
            self._wrap = 'newline'
        # enter indent and context
        self.gen.indent = self._indent + self._indent_tab
        self.gen.context = self
        self.gen.context_name = self.context_name
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # restore
        self.gen.indent = self._indent_prev
        self.gen.context = self._context_prev
        self.gen.context_name = self._context_name_prev

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


class Linebreak(CodeObject):
    """
    A marker to break lines in ClosureWithName when wrap='auto'.
    Outputs nothing itself. When encountered during rendering with wrap='auto',
    the items after the Linebreak are moved to a new line.
    Items before and after still follow wrap=auto.

    Examples::

        with gen.Dict('config').wrap('auto'):
            gen.Var('host', 'localhost')
            gen.Var('port', 8080)
            gen.Linebreak()
            gen.Var('timeout', 30)
            gen.Var('retries', 3)

    If total fits inline:

        config = {'host': 'localhost', 'port': 8080, 'timeout': 30, 'retries': 3}

    If total does not fit, groups are separated by newlines,
    each group independently follows wrap=auto::

        config = {'host': 'localhost', 'port': 8080,
            'timeout': 30, 'retries': 3,
        }
    """

    def generate(self):
        return


class GatherItems:
    """
    Gather Item/Var and convert to str.
    Each item's item_str already carries its own line_ending.
    Tokens are joined with a single space.
    """

    DEFAULT_WIDTH = 120

    def __init__(self, wrap="inline"):
        """
        Args:
            wrap (str | int): Wrapping mode.
                'inline': no wrap, all items on one line.
                'newline': each item on its own line.
                'expand': brackets on separate lines, items wrapped at DEFAULT_WIDTH.
                'auto': same as 'expand' (auto decision is in ClosureWithName).
                int: wrap at the given width.
        """
        self.items: "list[Item | Var]" = []
        self.wrap = wrap

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
        """
        Return a single string with all items joined on one line.
        Trailing line_ending (e.g. comma) is stripped.

        Returns:
            str: Joined inline string, or empty string if no items.
        """
        if not self.items:
            return ''
        result = ' '.join(item.item_str for item in self.items)
        if result.endswith(','):
            result = result[:-1]
        return result

    def iter_multiline(self):
        """
        Yield content rows respecting wrap mode and Linebreak markers.

        For ``'inline'``: yield a single row (all items joined).
        For ``'newline'``: yield each item as its own row.
        For ``'auto'`` / ``'expand'``: split at Linebreak boundaries into
        groups, pack each group independently at ``DEFAULT_WIDTH``.
        For ``int``: same as expand but using the integer width.

        Generated rows do NOT include indent prefix — the caller is
        responsible for adding indentation.

        Yields:
            str: Content row.
        """
        if self.wrap == 'inline':
            yield self.get_inline()
            return

        if not self.items:
            return

        if self.wrap == 'newline':
            for item in self.items:
                yield item.item_str.rstrip(',')
            return

        # Determine packing width from the items' own indent
        if isinstance(self.wrap, int):
            max_width = self.wrap
        else:
            max_width = self.DEFAULT_WIDTH
        indent_width = len(self.items[0].indent_str)
        remain_width = max_width - indent_width

        # Split items at Linebreak boundaries into groups
        groups = []
        current = []
        for item in self.items:
            if isinstance(item, Linebreak):
                if current:
                    groups.append(current)
                current = []
            else:
                current.append(item)
        if current:
            groups.append(current)

        if not groups:
            return

        if len(groups) > 1:
            # Multiple groups from Linebreak — render each independently
            for group in groups:
                if not group:
                    continue
                joined = ' '.join(i.item_str for i in group)
                if len(joined) <= remain_width:
                    yield joined
                else:
                    yield from self._iter_pack(group, remain_width)
        else:
            # Single group — regular wrapping
            yield from self._iter_pack(groups[0], remain_width)

    @staticmethod
    def _iter_pack(items, max_width):
        """
        Pack items with ``item_str`` into rows fitting within *max_width*.

        Each row preserves the trailing ``line_ending`` (e.g. comma) from
        the last item's ``item_str``.

        Yields:
            str: Content row (without indent prefix).
        """
        buffer = []
        count = 0
        for item in items:
            token = item.item_str
            if buffer:
                add_len = 1 + len(token)
                if count + add_len <= max_width:
                    buffer.append(token)
                    count += add_len
                else:
                    yield ' '.join(buffer)
                    buffer = [token]
                    count = len(token)
            else:
                buffer = [token]
                count = len(token)
        if buffer:
            yield ' '.join(buffer)
