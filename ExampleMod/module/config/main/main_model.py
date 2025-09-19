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
