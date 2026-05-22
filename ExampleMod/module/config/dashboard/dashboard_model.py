import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```


class Oil(m.Struct, omit_defaults=True):
    Time: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    Value: e.Annotated[int, m.Meta(ge=0, le=25000)] = 0


class Gems(m.Struct, omit_defaults=True):
    Time: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    Value: e.Annotated[int, m.Meta(ge=0)] = 0


class Progress(m.Struct, omit_defaults=True):
    Value: int = 0


class Planner(m.Struct, omit_defaults=True):
    GreenValue: int = 0
    GreenTotal: int = 0
