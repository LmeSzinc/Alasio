import typing as t

from alasio.config._index.config_generated import AlasioConfigGenerated

from ..const import entry

if t.TYPE_CHECKING:
    from ..alas import alas_model as alas
    from ..dashboard import dashboard_model as dashboard
    from ..general import general_model as general
    from ..main import main_model as main
    from ..opsi import opsi_model as opsi


class ConfigGenerated(AlasioConfigGenerated):
    # A generated config struct to fool IDE's type-predict and auto-complete
    entry = entry

    """
    ========== nav: alas ==========
    """
    # ----- Alas -----
    Game: "alas.Game"

    """
    ========== nav: dashboard ==========
    """
    # ----- Dashboard -----
    Oil: "dashboard.Oil"
    Gems: "dashboard.Gems"
    Progress: "dashboard.Progress"
    Planner: "dashboard.Planner"

    """
    ========== nav: general ==========
    """
    # ----- General -----
    Retirement: "general.Retirement"
    OneClickRetire: "general.OneClickRetire"
    Enhance: "general.Enhance"
    OldRetire: "general.OldRetire"
    DropRecord: "general.DropRecord"

    """
    ========== nav: main ==========
    """
    # ----- Main -----
    # Scheduler: "alasio.SchedulerUedit"
    Campaign: "main.Campaign"
    StopCondition: "main.StopCondition"
    Fleet1: "main.Fleet"
    Fleet2: "main.Fleet"
    Submarine: "main.Submarine"
    Emotion: "main.Emotion"
    HpControl: "main.HpControl"
    EnemyPriority: "main.EnemyPriority"
    Hard: "main.CampaignHard"

    """
    ========== nav: opsi ==========
    """
    # ----- OpsiExplore -----
    # Scheduler: "alasio.SchedulerU00"
    OpsiExplore: "opsi.OpsiExplore"
    OpsiGeneral: "opsi.OpsiGeneral"

    # ----- OpsiDaily -----
    # Scheduler: "alasio.SchedulerU00"
    OpsiDaily: "opsi.OpsiDaily"
    # OpsiGeneral: "opsi.OpsiGeneral"

    # ----- OpsiGeneral -----
    # OpsiGeneral: "opsi.OpsiGeneral"

    # ----- OpsiAshAssist -----
    # Scheduler: "alasio.SchedulerU00"
    OpsiAshAssist: "opsi.OpsiAshAssist"
