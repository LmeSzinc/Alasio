import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class OpsiAshAssist(a.GroupBase):
    Tier: e.Annotated[int, m.Meta(ge=1, le=15)] = 15


LITERAL_OpsiGeneral_BuyActionPointLimit = t.Literal[0, 1, 2, 3, 4, 5]


class OpsiGeneral(a.GroupBase):
    UseLogger: bool = True
    BuyActionPointLimit: LITERAL_OpsiGeneral_BuyActionPointLimit = 0
    OilLimit: e.Annotated[int, m.Meta(ge=500, le=20000)] = 1000
    RepairThreshold: e.Annotated[float, m.Meta(ge=0.0, le=1.0)] = 0.4
    DoRandomMapEvent: bool = True
    AkashiShopFilter: a.T_TUPLE_STR = ('ActionPoint', 'PurpleCoins')


LITERAL_OpsiAshBeacon_AttackMode = t.Literal['current', 'current_dossier']


class OpsiAshBeacon(a.GroupBase):
    AttackMode: LITERAL_OpsiAshBeacon_AttackMode = 'current'
    OneHitMode: bool = True
    DossierAutoAttackMode: bool = False
    RequestAssist: bool = True
    EnsureFullyCollected: bool = True


class OpsiFleetFilter(a.GroupBase):
    Filter: a.T_TUPLE_STR = ('Fleet-4', 'CallSubmarine', 'Fleet-2', 'Fleet-3', 'Fleet-1')


LITERAL_OpsiFleet_Fleet = t.Literal[1, 2, 3, 4]


class OpsiFleet(a.GroupBase):
    Fleet: LITERAL_OpsiFleet_Fleet = 1
    Submarine: bool = False


class OpsiExplore(a.GroupBase):
    SpecialRadar: bool = False
    ForceRun: bool = False
    LastZone: int = 0


LITERAL_OpsiShop_PresetFilter = t.Literal['max_benefit', 'max_benefit_meta', 'no_meta', 'all', 'custom']


class OpsiShop(a.GroupBase):
    PresetFilter: LITERAL_OpsiShop_PresetFilter = 'max_benefit_meta'
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


LITERAL_OpsiMonthBoss_Mode = t.Literal['normal', 'normal_hard']


class OpsiMonthBoss(a.GroupBase):
    Mode: LITERAL_OpsiMonthBoss_Mode = 'normal'
    CheckAdaptability: bool = True
    ForceRun: bool = False


LITERAL_OpsiMeowfficerFarming_HazardLevel = t.Literal[3, 4, 5, 6, 10]


class OpsiMeowfficerFarming(a.GroupBase):
    ActionPointPreserve: int = 1000
    HazardLevel: LITERAL_OpsiMeowfficerFarming_HazardLevel = 5


LITERAL_OpsiHazard1Leveling_TargetZone = t.Literal[0, 44, 22]


class OpsiHazard1Leveling(a.GroupBase):
    TargetZone: LITERAL_OpsiHazard1Leveling_TargetZone = 0
