import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class Campaign(a.GroupBase):
    Name: str = '12-4'
    Event: t.Literal['campaign_main'] = 'campaign_main'
    Mode: t.Literal['normal', 'hard'] = 'normal'
    UseClearMode: bool = True
    UseFleetLock: bool = True
    UseAutoSearch: bool = True
    Use2xBook: bool = False
    AmbushEvade: bool = True


class CampaignHard(Campaign):
    Event: t.Literal['campaign_main'] = 'campaign_main'
    Mode: t.Literal['normal'] = 'normal'


class StopCondition(a.GroupBase):
    OilLimit: int = 1000
    RunCount: int = 0
    MapAchievement: t.Literal[
        'non_stop', '100_percent_clear', 'map_3_stars', 'threat_safe', 'threat_safe_without_3_stars',
    ] = 'non_stop'
    StageIncrease: bool = False
    GetNewShip: bool = False
    ReachLevel: int = 0


class Fleet(a.GroupBase):
    Fleet: t.Literal[1, 2, 3, 4, 5, 6] = 1
    Formation: t.Literal['line_ahead', 'double_line', 'diamond'] = 'double_line'
    FleetMode: t.Literal[
        'combat_auto', 'combat_manual', 'stand_still_in_the_middle', 'hide_in_bottom_left',
    ] = 'combat_auto'
    FleetStep: t.Literal[2, 3, 4, 5] = 3


class Submarine(a.GroupBase):
    Fleet: t.Literal[0, 1, 2] = 0
    Mode: t.Literal['do_not_use', 'hunt_only', 'boss_only', 'hunt_and_boss', 'every_combat'] = 'do_not_use'
    AutoSearchMode: t.Literal['sub_standby', 'sub_auto_call'] = 'sub_standby'
    DistanceToBoss: t.Literal[
        'to_boss_position', '1_grid_to_boss', '2_grid_to_boss', 'use_open_ocean_support',
    ] = '2_grid_to_boss'


class Emotion(a.GroupBase):
    Mode: t.Literal['calculate', 'ignore', 'calculate_ignore'] = 'calculate'


class EmotionRecord(a.DashboardAmount):
    Value: e.Annotated[int, m.Meta(ge=0, le=150)] = 119
    Control: t.Literal[
        'keep_exp_bonus', 'prevent_green_face', 'prevent_yellow_face', 'prevent_red_face',
    ] = 'prevent_yellow_face'
    Recover: t.Literal['not_in_dormitory', 'dormitory_floor_1', 'dormitory_floor_2'] = 'not_in_dormitory'
    Oath: bool = False

    @property
    def speed(self):
        """
        Returns:
            int: recover speed per 6 min
        """
        recover = self.Recover
        if recover == 'dormitory_floor_2':
            speed = 50
        elif recover == 'dormitory_floor_1':
            speed = 40
        else:
            speed = 20
        if self.Oath:
            speed += 10
        return speed // 10

    @property
    def limit(self):
        """
        Returns:
            int: Minimum emotion value to control
        """
        control = self.Control
        if control == 'keep_exp_bonus':
            return 120
        if control == 'prevent_green_face':
            return 40
        if control == 'prevent_yellow_face':
            return 30
        # let's just don't be that harsh
        return 2

    @property
    def max(self):
        """
        Returns:
            int: Maximum emotion value
        """
        recover = self.Recover
        if recover == 'dormitory_floor_2' or recover == 'dormitory_floor_1':
            return 150
        return 119

    def post_edit(self, old: e.Self, edits):
        if self.Control == 'keep_exp_bonus':
            recover = self.Recover
            if recover == 'dormitory_floor_2' or recover == 'dormitory_floor_1':
                pass
            else:
                raise m.ValidationError(
                    'EmotionControl="Keep Happy Bonus" and RecoverLocation="Docks" can not be used together')


class HpControl(a.GroupBase):
    UseHpBalance: bool = False
    UseEmergencyRepair: bool = False
    UseLowHpRetreat: bool = False
    HpBalanceThreshold: float = 0.2
    HpBalanceWeight: str = '1000, 1000, 1000'
    RepairUseSingleThreshold: float = 0.3
    RepairUseMultiThreshold: float = 0.6
    LowHpRetreatThreshold: float = 0.3


class EnemyPriority(a.GroupBase):
    EnemyScaleBalanceWeight: t.Literal['default_mode', 'S3_enemy_first', 'S1_enemy_first'] = 'default_mode'


class GemsFarming(a.GroupBase):
    ChangeFlagship: t.Literal['ship', 'ship_equip'] = 'ship'
    CommonCV: t.Literal['any', 'langley', 'bogue', 'ranger', 'hermes'] = 'any'
    ChangeVanguard: t.Literal['disabled', 'ship', 'ship_equip'] = 'ship'
    CommonDD: t.Literal['any', 'favourite', 'aulick_or_foote', 'cassin_or_downes', 'z20_or_z21'] = 'any'
    CommissionLimit: bool = True
