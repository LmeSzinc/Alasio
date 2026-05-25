import datetime as d
import typing as t

import alasio.config.group_base as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class Oil(a.DashboardTotal):
    Value: e.Annotated[int, m.Meta(ge=0, le=25000)] = 0


class Gems(a.DashboardAmount):
    pass


class Progress(a.GroupBase):
    Value: int = 0


class Planner(a.GroupBase):
    GreenValue: int = 0
    GreenTotal: int = 0
