import datetime as d

import msgspec as m
import typing_extensions as e

from alasio.config.const import DataInconsistent
from alasio.ext.msgspec_error.parse_anno import get_msgspec_annotation

DEFAULT_TIME = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
T_TIME = e.Annotated[d.datetime, m.Meta(tz=True)]
T_INT_GE0 = e.Annotated[int, m.Meta(ge=0)]


def parse_literal_arg(arg):
    """
    Parse literal arg like:
    'normal' -> normal

    Args:
        arg (str):
    
    Returns:
        str:
    """
    if arg.startswith("'") and arg.endswith("'"):
        return arg[1:-1]
    if arg.startswith('"') and arg.endswith('"'):
        return arg[1:-1]
    return arg


def parse_literal_string(anno):
    """
    Parse literal string like:
    t.Literal[('normal', 'hard')] -> ['normal', 'hard']
    t.Literal['normal', 'hard'] -> ['normal', 'hard']
    Literal['normal', 'hard'] -> ['normal', 'hard']
    typing.Literal['normal', 'hard'] -> ['normal', 'hard']

    Args:
        anno (str):

    Returns:
        list[str]:
    
    Raises:
        ValueError: If anno is not a literal
    """
    if not anno.startswith(('Literal', 't.Literal', 'typing.Literal')):
        raise ValueError('Annotation is not a literal')
    args = anno.partition('[')[2].rpartition(']')[0]
    if args.startswith('(') and args.endswith(')'):
        args = args[1:-1]
    return [parse_literal_arg(arg.strip()) for arg in args.split(',')]


class GroupBase(m.Struct, omit_defaults=True, dict=True):
    @classmethod
    def get_meta(cls, attr) -> m.Meta:
        """
        Args:
            attr (str):
        """
        try:
            anno = get_msgspec_annotation(cls, attr)
        except AttributeError:
            raise DataInconsistent(f'Missing annotation for {cls}.{attr}')
        try:
            annotated_args = anno.__metadata__
        except AttributeError:
            raise DataInconsistent(f'Missing anno.__metadata__ for {cls}.{attr}')
        for arg in annotated_args:
            if isinstance(arg, m.Meta):
                return arg
        raise DataInconsistent(f'Missing msgspec.Meta for {cls}.{attr}')

    @classmethod
    def get_default(cls, attr):
        """
        Args:
            attr (str):
        """
        try:
            return getattr(cls, attr)
        except AttributeError:
            raise DataInconsistent(f'Missing default for {cls}.{attr}')

    @classmethod
    def get_option(cls, attr):
        """
        Get options if attr is a literal

        Args:
            attr (str):
        """
        try:
            anno = get_msgspec_annotation(cls, attr)
        except AttributeError:
            raise DataInconsistent(f'Missing annotation for {cls}.{attr}')
        # t.Literal[('normal', 'hard')]
        # usually because of `from __future__ import annotations`
        if isinstance(anno, str):
            try:
                return parse_literal_string(anno)
            except ValueError:
                raise DataInconsistent(f'Arg is not a literal: {cls}.{attr}')
        # typing.Literal object
        literal_args = getattr(anno, '__args__', None)
        if literal_args is not None:
            return list(literal_args)
        raise DataInconsistent(f'Arg is not a literal: {cls}.{attr}')


def cap_value(value, meta):
    """
    Cap value to meta range
    Note that only ge and le are supported

    Args:
        value (int): value to cap
        meta (msgspec.Meta): msgspec.Meta object

    Returns:
        value (int): capped value
    """
    if meta.ge is not None and value < meta.ge:
        value = meta.ge
    if meta.le is not None and value > meta.le:
        value = meta.le
    return value
