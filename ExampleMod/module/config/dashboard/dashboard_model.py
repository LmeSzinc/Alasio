import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class Oil(m.Struct, omit_defaults=True):
    Value: e.Annotated[int, m.Meta(ge=0, le=25000)] = 0
    Total: t.Literal[25000] = 25000
    Time: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)


class Gems(m.Struct, omit_defaults=True):
    Value: e.Annotated[int, m.Meta(ge=0)] = 0
    Time: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)


class Progress(m.Struct, omit_defaults=True):
    Value: e.Annotated[float, m.Meta(ge=0)] = 0
    Time: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)


class Planner(m.Struct, omit_defaults=True):
    Progress: e.Annotated[int, m.Meta(ge=0)] = 0
    Eta: str = ''
    GreenValue: e.Annotated[int, m.Meta(ge=0)] = 0
    GreenTotal: e.Annotated[int, m.Meta(ge=0)] = 0
    Time: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
