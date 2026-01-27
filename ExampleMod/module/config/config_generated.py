import typing

from alasio.config.config_generated import ConfigGenerated as AlasioConfigBase
from .const import entry

if typing.TYPE_CHECKING:
    from .alas import alas_model as alas
    from .general import general_model as general
    from .main import main_model as main
    from .opsi import opsi_model as opsi


class ConfigGenerated(AlasioConfigBase):
    # A generated config struct to fool IDE's type-predict and auto-complete
    entry = entry

    # alas
    Game: "alas.Game"

    # general
    DropRecord: "general.DropRecord"
    Retirement: "general.Retirement"
    OneClickRetire: "general.OneClickRetire"
    Enhance: "general.Enhance"
    OldRetire: "general.OldRetire"

    # main
    Campaign: "main.Campaign"
    StopCondition: "main.StopCondition"
    Fleet1: "main.Fleet1"
    Fleet2: "main.Fleet2"
    Submarine: "main.Submarine"
    Emotion: "main.Emotion"
    HpControl: "main.HpControl"
    EnemyPriority: "main.EnemyPriority"
    GemsFarming: "main.GemsFarming"

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
