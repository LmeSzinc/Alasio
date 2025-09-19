import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class OpsiAshAssist(m.Struct, omit_defaults=True):
    Tier: e.Annotated[int, m.Meta(ge=1, le=15)] = 15


class OpsiGeneral(m.Struct, omit_defaults=True):
    UseLogger: bool = True
    BuyActionPointLimit: t.Literal[0, 1, 2, 3, 4, 5] = 0
    OilLimit: e.Annotated[int, m.Meta(ge=500, le=20000)] = 1000
    RepairThreshold: e.Annotated[float, m.Meta(ge=0.0, le=1.0)] = 0.4
    DoRandomMapEvent: bool = True
    AkashiShopFilter: t.Tuple[str] = ('ActionPoint', 'PurpleCoins')


class OpsiAshBeacon(m.Struct, omit_defaults=True):
    AttackMode: t.Literal['current', 'current_dossier'] = 'current'
    OneHitMode: bool = True
    DossierAutoAttackMode: bool = False
    RequestAssist: bool = True
    EnsureFullyCollected: bool = True


class OpsiFleetFilter(m.Struct, omit_defaults=True):
    Filter: t.Tuple[str] = ('Fleet-4', 'CallSubmarine', 'Fleet-2', 'Fleet-3', 'Fleet-1')


class OpsiFleet(m.Struct, omit_defaults=True):
    Fleet: t.Literal[1, 2, 3, 4] = 1
    Submarine: bool = False


class OpsiExplore(m.Struct, omit_defaults=True):
    SpecialRadar: bool = False
    ForceRun: bool = False
    LastZone: int = 0


class OpsiShop(m.Struct, omit_defaults=True):
    PresetFilter: t.Literal[
        'max_benefit',
        'max_benefit_meta',
        'no_meta',
        'all',
        'custom',
    ] = 'max_benefit_meta'
    CustomFilter: t.Tuple[str] = (
        'LoggerAbyssalT6', 'LoggerAbyssalT5', 'LoggerObscure', 'LoggerAbyssalT4', 'ActionPoint', 'PurpleCoins',
        'GearDesignPlanT3', 'PlateRandomT4', 'DevelopmentMaterialT3', 'GearDesignPlanT2', 'GearPart',
        'OrdnanceTestingReportT3', 'OrdnanceTestingReportT2', 'DevelopmentMaterialT2', 'OrdnanceTestingReportT1',
        'METARedBook', 'CrystallizedHeatResistantSteel', 'NanoceramicAlloy', 'NeuroplasticProstheticArm',
        'SupercavitationGenerator',
    )


class OpsiVoucher(m.Struct, omit_defaults=True):
    Filter: t.Tuple[str] = ('LoggerAbyssal', 'LoggerObscure', 'Book', 'Coin', 'Fragment')


class OpsiDaily(m.Struct, omit_defaults=True):
    DoMission: bool = True
    UseTuningSample: bool = True


class OpsiObscure(m.Struct, omit_defaults=True):
    ForceRun: bool = False


class OpsiAbyssal(m.Struct, omit_defaults=True):
    ForceRun: bool = False


class OpsiStronghold(m.Struct, omit_defaults=True):
    ForceRun: bool = False


class OpsiMonthBoss(m.Struct, omit_defaults=True):
    Mode: t.Literal['normal', 'normal_hard'] = 'normal'
    CheckAdaptability: bool = True
    ForceRun: bool = False


class OpsiMeowfficerFarming(m.Struct, omit_defaults=True):
    ActionPointPreserve: int = 1000
    HazardLevel: t.Literal[3, 4, 5, 6, 10] = 5


class OpsiHazard1Leveling(m.Struct, omit_defaults=True):
    TargetZone: t.Literal[0, 44, 22] = 0
