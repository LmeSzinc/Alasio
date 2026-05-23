import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class Scheduler(m.Struct, omit_defaults=True):
    Enable: bool = False
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: t.Literal['00:00'] = '00:00'


class SchedulerU00(m.Struct, omit_defaults=True):
    Enable: bool = False
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: t.Literal['00:00'] = '00:00'


class SchedulerStatic(m.Struct, omit_defaults=True):
    Enable: t.Literal['enabled'] = 'enabled'
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: t.Literal['00:00'] = '00:00'


class SchedulerStaticU00(m.Struct, omit_defaults=True):
    Enable: t.Literal['enabled'] = 'enabled'
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: t.Literal['00:00'] = '00:00'


class SchedulerUedit(m.Struct, omit_defaults=True):
    Enable: bool = False
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: str = '00:00'


class SchedulerStaticUedit(m.Struct, omit_defaults=True):
    Enable: t.Literal['enabled'] = 'enabled'
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: str = '00:00'


class SchedulerU04(m.Struct, omit_defaults=True):
    Enable: bool = False
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: t.Literal['04:00'] = '04:00'


class SchedulerStaticU04(m.Struct, omit_defaults=True):
    Enable: t.Literal['enabled'] = 'enabled'
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: t.Literal['04:00'] = '04:00'
