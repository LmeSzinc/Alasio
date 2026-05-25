import datetime as d
import typing as t

import alasio.config.group_base as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class OpsiAshAssist(a.GroupBase):
    Tier: e.Annotated[int, m.Meta(ge=1, le=15)] = 15


class OpsiGeneral(a.GroupBase):
    UseLogger: bool = True
    BuyActionPointLimit: t.Literal[0, 1, 2, 3, 4, 5] = 0
    OilLimit: e.Annotated[int, m.Meta(ge=500, le=20000)] = 1000
    RepairThreshold: e.Annotated[float, m.Meta(ge=0.0, le=1.0)] = 0.4
    DoRandomMapEvent: bool = True
    AkashiShopFilter: a.T_TUPLE_STR = ('ActionPoint', 'PurpleCoins')


class OpsiAshBeacon(a.GroupBase):
    AttackMode: t.Literal['current', 'current_dossier'] = 'current'
    OneHitMode: bool = True
    DossierAutoAttackMode: bool = False
    RequestAssist: bool = True
    EnsureFullyCollected: bool = True


class OpsiFleetFilter(a.GroupBase):
    Filter: a.T_TUPLE_STR = ('Fleet-4', 'CallSubmarine', 'Fleet-2', 'Fleet-3', 'Fleet-1')


class OpsiFleet(a.GroupBase):
    Fleet: t.Literal[1, 2, 3, 4] = 1
    Submarine: bool = False


class OpsiExplore(a.GroupBase):
    SpecialRadar: bool = False
    ForceRun: bool = False
    LastZone: int = 0


class OpsiShop(a.GroupBase):
    PresetFilter: t.Literal['max_benefit', 'max_benefit_meta', 'no_meta', 'all', 'custom'] = 'max_benefit_meta'
    CustomFilter: a.T_TUPLE_STR = (
        'LoggerAbyssalT6', 'LoggerAbyssalT5', 'LoggerObscure', 'LoggerAbyssalT4', 'ActionPoint', 'PurpleCoins',
        'GearDesignPlanT3', 'PlateRandomT4', 'DevelopmentMaterialT3', 'GearDesignPlanT2', 'GearPart',
        'OrdnanceTestingReportT3', 'OrdnanceTestingReportT2', 'DevelopmentMaterialT2', 'OrdnanceTestingReportT1',
        'METARedBook', 'CrystallizedHeatResistantSteel', 'NanoceramicAlloy', 'NeuroplasticProstheticArm',
        'SupercavitationGenerator',
    )


class OpsiVoucher(a.GroupBase):
    Filter: a.T_TUPLE_STR = ('LoggerAbyssal', 'LoggerObscure', 'Book', 'Coin', 'Fragment')


class OpsiDaily(a.GroupBase):
    DoMission: bool = True
    UseTuningSample: bool = True


class OpsiObscure(a.GroupBase):
    ForceRun: bool = False


class OpsiAbyssal(a.GroupBase):
    ForceRun: bool = False


class OpsiStronghold(a.GroupBase):
    ForceRun: bool = False


class OpsiMonthBoss(a.GroupBase):
    Mode: t.Literal['normal', 'normal_hard'] = 'normal'
    CheckAdaptability: bool = True
    ForceRun: bool = False


class OpsiMeowfficerFarming(a.GroupBase):
    ActionPointPreserve: int = 1000
    HazardLevel: t.Literal[3, 4, 5, 6, 10] = 5


class OpsiHazard1Leveling(a.GroupBase):
    TargetZone: t.Literal[0, 44, 22] = 0
