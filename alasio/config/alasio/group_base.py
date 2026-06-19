import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e
from msgspecerror import get_msgspec_annotation

from alasio.backport.literal import parse_literal_string
from alasio.config.alasio.group_proxy import batch_set
from alasio.config.const import DataInconsistent

if t.TYPE_CHECKING:
    from alasio.base.servertime import ServerTime

DEFAULT_TIME = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
T_DATETIME = e.Annotated[d.datetime, m.Meta(tz=True)]
T_INT_GE0 = e.Annotated[int, m.Meta(ge=0)]
T_TUPLE_STR = t.Tuple[str, ...]


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

        Returns:
            tuple:
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
            return literal_args
        raise DataInconsistent(f'Arg is not a literal: {cls}.{attr}')

    @batch_set
    def get_servertime(self) -> "ServerTime":
        """
        To access config.servertime in group model, so we can use group.is_expired()
        This is something magic that:
        GroupProxy.__getattr__ has method redirection: `attr.__func__(self, ...)`
        The `self` of @batch_set decorated method is GroupProxy object, not DashboardBase(msgspec.Struct) object
        So when using `self.servertime`, it looks up GroupProxy.servertime, and returns GroupProxy._config.servertime

        Notes to keep this magic, every method and its parent caller which access to servertime
        - must be decorated by @batch_set
        - must be used with config, must not be used individually
        """
        try:
            return self.servertime
        except AttributeError:
            raise DataInconsistent(f'Missing servertime injection. '
                                   f'To access servertime, group model must be wrapped in GroupProxy')

    def post_edit(self, old: e.Self, edits):
        """
        Callback method whenever user edits config at frontend.
        New value is set to current object when this method is called.

        Args:
            old: Group model with old values
            edits (dict[str, Any]):
                New values, key: arg_name, value: arg value

        Raises:
            msgspec.ValidationError:
                Raise error to reject user edits
            Exceptions:
                Any other errors are considered as internal failures
        """
        pass
