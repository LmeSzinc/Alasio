import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class Scheduler(m.Struct, omit_defaults=True):
    Enable: bool = False
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: str = '00:00'


class SchedulerStatic(m.Struct, omit_defaults=True):
    Enable: t.Literal[True] = True
    NextRun: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    ServerUpdate: str = '00:00'
