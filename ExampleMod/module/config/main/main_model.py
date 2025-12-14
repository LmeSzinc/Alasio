import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class Campaign(m.Struct, omit_defaults=True):
    Name: str = '12-4'
    Event: t.Literal['campaign_main'] = 'campaign_main'
    Mode: t.Literal['normal', 'hard'] = 'normal'
    UseClearMode: bool = True
    UseFleetLock: bool = True
    UseAutoSearch: bool = True
    Use2xBook: bool = False
    AmbushEvade: bool = True


class StopCondition(m.Struct, omit_defaults=True):
    OilLimit: int = 1000
    RunCount: int = 0
    MapAchievement: t.Literal[
        'non_stop',
        '100_percent_clear',
        'map_3_stars',
        'threat_safe',
        'threat_safe_without_3_stars',
    ] = 'non_stop'
    StageIncrease: bool = False
    GetNewShip: bool = False
    ReachLevel: int = 0


class Fleet1(m.Struct, omit_defaults=True):
    Fleet: t.Literal[1, 2, 3, 4, 5, 6] = 1
    Formation: t.Literal['line_ahead', 'double_line', 'diamond'] = 'double_line'
    FleetMode: t.Literal[
        'combat_auto',
        'combat_manual',
        'stand_still_in_the_middle',
        'hide_in_bottom_left',
    ] = 'combat_auto'
    FleetStep: t.Literal[2, 3, 4, 5] = 3


class Fleet2(m.Struct, omit_defaults=True):
    Fleet: t.Literal[1, 2, 3, 4, 5, 6] = 1
    Formation: t.Literal['line_ahead', 'double_line', 'diamond'] = 'double_line'
    FleetMode: t.Literal[
        'combat_auto',
        'combat_manual',
        'stand_still_in_the_middle',
        'hide_in_bottom_left',
    ] = 'combat_auto'
    FleetStep: t.Literal[2, 3, 4, 5] = 3


class Submarine(m.Struct, omit_defaults=True):
    Fleet: t.Literal[0, 1, 2] = 0
    Mode: t.Literal[
        'do_not_use',
        'hunt_only',
        'boss_only',
        'hunt_and_boss',
        'every_combat',
    ] = 'do_not_use'
    AutoSearchMode: t.Literal['sub_standby', 'sub_auto_call'] = 'sub_standby'
    DistanceToBoss: t.Literal[
        'to_boss_position',
        '1_grid_to_boss',
        '2_grid_to_boss',
        'use_open_ocean_support',
    ] = '2_grid_to_boss'


class Emotion(m.Struct, omit_defaults=True):
    Mode: t.Literal['calculate', 'ignore', 'calculate_ignore'] = 'calculate'
    Fleet1Value: int = 119
    Fleet1Record: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    Fleet1Control: t.Literal[
        'keep_exp_bonus',
        'prevent_green_face',
        'prevent_yellow_face',
        'prevent_red_face',
    ] = 'prevent_yellow_face'
    Fleet1Recover: t.Literal[
        'not_in_dormitory',
        'dormitory_floor_1',
        'dormitory_floor_2',
    ] = 'not_in_dormitory'
    Fleet1Oath: bool = False
    Fleet2Value: int = 119
    Fleet2Record: e.Annotated[d.datetime, m.Meta(tz=True)] = d.datetime(2020, 1, 1, 0, 0, tzinfo=d.timezone.utc)
    Fleet2Control: t.Literal[
        'keep_exp_bonus',
        'prevent_green_face',
        'prevent_yellow_face',
        'prevent_red_face',
    ] = 'prevent_yellow_face'
    Fleet2Recover: t.Literal[
        'not_in_dormitory',
        'dormitory_floor_1',
        'dormitory_floor_2',
    ] = 'not_in_dormitory'
    Fleet2Oath: bool = False


class HpControl(m.Struct, omit_defaults=True):
    UseHpBalance: bool = False
    UseEmergencyRepair: bool = False
    UseLowHpRetreat: bool = False
    HpBalanceThreshold: float = 0.2
    HpBalanceWeight: str = '1000, 1000, 1000'
    RepairUseSingleThreshold: float = 0.3
    RepairUseMultiThreshold: float = 0.6
    LowHpRetreatThreshold: float = 0.3


class EnemyPriority(m.Struct, omit_defaults=True):
    EnemyScaleBalanceWeight: t.Literal[
        'default_mode',
        'S3_enemy_first',
        'S1_enemy_first',
    ] = 'default_mode'


class GemsFarming(m.Struct, omit_defaults=True):
    ChangeFlagship: t.Literal['ship', 'ship_equip'] = 'ship'
    CommonCV: t.Literal['any', 'langley', 'bogue', 'ranger', 'hermes'] = 'any'
    ChangeVanguard: t.Literal['disabled', 'ship', 'ship_equip'] = 'ship'
    CommonDD: t.Literal[
        'any',
        'favourite',
        'aulick_or_foote',
        'cassin_or_downes',
        'z20_or_z21',
    ] = 'any'
    CommissionLimit: bool = True
