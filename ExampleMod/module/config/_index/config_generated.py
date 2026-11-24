import typing as t

from alasio.config.base import AlasioConfigBase

from .mod_entry import MOD_ENTRY

if t.TYPE_CHECKING:
    from ..alas import alas_model as alas
    from ..dashboard import dashboard_model as dashboard
    from ..gems import gems_model as gems
    from ..general import general_model as general
    from ..main import main_model as main
    from ..opsi import opsi_model as opsi
    from ..reward import reward_model as reward
    from ..shop import shop_model as shop


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class ConfigGenerated(AlasioConfigBase):
    """
    A generated config struct to fool IDE's type-predict and auto-complete
    """
    entry = MOD_ENTRY

    """
    ========== nav: alas ==========
    """
    # ----- Alas -----
    Game: "alas.Game"

    """
    ========== nav: general ==========
    """
    # ----- General -----
    Retirement: "general.Retirement"
    OneClickRetire: "general.OneClickRetire"
    Enhance: "general.Enhance"
    OldRetire: "general.OldRetire"
    DropRecord: "general.DropRecord"
    ShopDemo: "general.ShopDemo"

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
    Emotion1: "main.EmotionRecord"
    Emotion2: "main.EmotionRecord"
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

    """
    ========== nav: gems ==========
    """
    # ----- GemsFarming -----
    # Scheduler: "alasio.Scheduler"
    # Campaign: "gems.GemsCampaign"
    GemsFarming: "gems.GemsFarming"
    # StopCondition: "gems.GemsStopCondition"
    # Fleet1: "main.Fleet"
    # Submarine: "gems.GemsSubmarine"
    # Emotion1: "gems.GemsEmotionRecord"
    EquipmentCode: "gems.EquipmentCode"

    """
    ========== nav: shop ==========
    """
    # ----- GeneralShop -----
    # Scheduler: "shop.SchedulerGeneralShop"
    GeneralShop: "shop.GeneralShop"

    # ----- GuildShop -----
    # Scheduler: "alasio.SchedulerU00"
    GuildShop: "shop.GuildShop"

    # ----- MedalShop -----
    # Scheduler: "alasio.SchedulerU00"
    MedalShop: "shop.MedalShop"

    # ----- MeritShop -----
    # Scheduler: "alasio.SchedulerU00"
    MeritShop: "shop.MeritShop"

    # ----- CoreShop -----
    # Scheduler: "alasio.SchedulerU00"
    CoreShop: "shop.CoreShop"

    """
    ========== nav: reward ==========
    """
    # ----- Commission -----
    # Scheduler: "alasio.SchedulerU00"
    Commission: "reward.Commission"

    # ----- Tactical -----
    # Scheduler: "alasio.SchedulerU00"
    Tactical: "reward.Tactical"
    ControlExpOverflow: "reward.ControlExpOverflow"
    AddNewStudent: "reward.AddNewStudent"

    # ----- Research -----
    # Scheduler: "alasio.SchedulerU00"
    Research: "reward.Research"

    """
    ========== nav: dashboard ==========
    """
    # ----- Dashboard -----
    Oil: "dashboard.Oil"
    Coin: "dashboard.Coin"
    Gems: "dashboard.Gems"
    Progress: "dashboard.Progress"
    Planner: "dashboard.Planner"
