from alasio.base.timer import getnow
from alasio.config.alasio.alasio_model import (
    Scheduler,
    SchedulerStatic,
    SchedulerStaticU00,
    SchedulerStaticU04,
    SchedulerStaticUedit,
    SchedulerU00,
    SchedulerU04,
    SchedulerUedit,
)
from alasio.config.alasio.device_model import Emulator, EmulatorInfo, Error, Optimization
from alasio.config.alasio.group_base import (
    DEFAULT_TIME,
    DataInconsistent,
    GroupBase,
    T_DATETIME,
    T_INT_GE0,
    T_TUPLE_STR,
)
from alasio.config.alasio.group_proxy import batch_set
from alasio.config.alasio.store_model import (
    DashboardAmount,
    DashboardBase,
    DashboardDynamicTotal,
    DashboardRemain,
    DashboardTotal,
)

# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config_dev.gen_alasio ```
