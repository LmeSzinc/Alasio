import typing as t

from alasio.config.config_generated import AlasioConfigGenerated

from .const import entry

if t.TYPE_CHECKING:
    from .alas import alas_model as alas
    from .dashboard import dashboard_model as dashboard
    from .general import general_model as general
    from .main import main_model as main
    from .opsi import opsi_model as opsi


class ConfigGenerated(AlasioConfigGenerated):
    # A generated config struct to fool IDE's type-predict and auto-complete
    entry = entry

    # alas
    Game: "alas.Game"

    # dashboard
    Progress: "dashboard.Progress"
    Planner: "dashboard.Planner"

    # general
    Retirement: "general.Retirement"
    OneClickRetire: "general.OneClickRetire"
    Enhance: "general.Enhance"
    OldRetire: "general.OldRetire"
    DropRecord: "general.DropRecord"

    # main
    Campaign: "main.Campaign"
    StopCondition: "main.StopCondition"
    Fleet1: "main.Fleet"
    Fleet2: "main.Fleet"
    Submarine: "main.Submarine"
    Emotion: "main.Emotion"
    HpControl: "main.HpControl"
    EnemyPriority: "main.EnemyPriority"

    # opsi
    OpsiExplore: "opsi.OpsiExplore"
    OpsiGeneral: "opsi.OpsiGeneral"
    OpsiDaily: "opsi.OpsiDaily"
    OpsiGeneral: "opsi.OpsiGeneral"
    OpsiGeneral: "opsi.OpsiGeneral"
    OpsiAshAssist: "opsi.OpsiAshAssist"
