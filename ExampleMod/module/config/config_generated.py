import typing

from alasio.config.base import AlasioConfigBase
from const import entry


if typing.TYPE_CHECKING:
    from .main import main_model as main
    from .opsi import opsi_model as opsi
    from .scheduler import scheduler_model as scheduler


class ConfigGenerated(AlasioConfigBase):
    # A generated config struct to fool IDE's type-predict and auto-complete
    entry = entry

    # main
    Campaign: "main.Campaign"
    StopCondition: "main.StopCondition"
    Fleet1: "main.Fleet1"
    Fleet2: "main.Fleet2"

    # opsi
    OpsiAshAssist: "opsi.OpsiAshAssist"
    OpsiGeneral: "opsi.OpsiGeneral"
    OpsiAshBeacon: "opsi.OpsiAshBeacon"
    OpsiFleetFilter: "opsi.OpsiFleetFilter"
    OpsiFleet: "opsi.OpsiFleet"
    OpsiExplore: "opsi.OpsiExplore"
    OpsiShop: "opsi.OpsiShop"
    OpsiVoucher: "opsi.OpsiVoucher"
    OpsiDaily: "opsi.OpsiDaily"
    OpsiObscure: "opsi.OpsiObscure"
    OpsiAbyssal: "opsi.OpsiAbyssal"
    OpsiStronghold: "opsi.OpsiStronghold"
    OpsiMonthBoss: "opsi.OpsiMonthBoss"
    OpsiMeowfficerFarming: "opsi.OpsiMeowfficerFarming"
    OpsiHazard1Leveling: "opsi.OpsiHazard1Leveling"

    # scheduler
    Scheduler: "scheduler.Scheduler"
