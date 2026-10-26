import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class Oil(a.DashboardTotal):
    Value: e.Annotated[int, m.Meta(ge=0, le=25000)] = 0


class Gems(a.DashboardAmount):
    pass


class Progress(a.DashboardProgress):
    pass


class Planner(a.DashboardPlanner):
    GreenValue: a.T_INT_GE0 = 0
    GreenTotal: a.T_INT_GE0 = 0
