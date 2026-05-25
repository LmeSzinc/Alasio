import datetime as d
import typing as t

import alasio.config.alasio.group_base as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config_dev.gen_alasio ```

class Scheduler(a.GroupBase):
    Enable: bool = False
    NextRun: a.T_DATETIME = a.DEFAULT_TIME
    ServerUpdate: t.Literal['00:00'] = '00:00'


class SchedulerU00(Scheduler):
    pass


class SchedulerStatic(Scheduler):
    Enable: t.Literal['enabled'] = 'enabled'


class SchedulerStaticU00(SchedulerStatic):
    pass


class SchedulerUedit(Scheduler):
    ServerUpdate: str = '00:00'


class SchedulerStaticUedit(SchedulerStatic, SchedulerUedit):
    pass


class SchedulerU04(Scheduler):
    ServerUpdate: t.Literal['04:00'] = '04:00'


class SchedulerStaticU04(SchedulerStatic, SchedulerU04):
    pass
