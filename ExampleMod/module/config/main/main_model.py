import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

LITERAL_Campaign_Name = t.Literal[
    '1-1', '1-2', '1-3', '1-4',
    '2-1', '2-2', '2-3', '2-4',
    '3-1', '3-2', '3-3', '3-4',
    '4-1', '4-2', '4-3', '4-4',
    '5-1', '5-2', '5-3', '5-4',
    '6-1', '6-2', '6-3', '6-4',
    '7-1', '7-2', '7-3', '7-4',
    '8-1', '8-2', '8-3', '8-4',
    '9-1', '9-2', '9-3', '9-4',
    '10-1', '10-2', '10-3', '10-4',
    '11-1', '11-2', '11-3', '11-4',
    '12-1', '12-2', '12-3', '12-4',
    '13-1', '13-2', '13-3', '13-4',
    '14-1', '14-2', '14-3', '14-4',
    '15-1', '15-2', '15-3', '15-4',
]
LITERAL_Campaign_Event = t.Literal['campaign_main']
LITERAL_Campaign_Mode = t.Literal['normal', 'hard']


class Campaign(a.GroupBase):
    Name: LITERAL_Campaign_Name = '12-4'
    Event: LITERAL_Campaign_Event = 'campaign_main'
    Mode: LITERAL_Campaign_Mode = 'normal'
    UseClearMode: bool = True
    UseFleetLock: bool = True
    UseAutoSearch: bool = True
    Use2xBook: bool = False
    AmbushEvade: bool = True


LITERAL_CampaignHard_Mode = t.Literal['normal']


class CampaignHard(Campaign):
    Event: LITERAL_Campaign_Event = 'campaign_main'
    Mode: LITERAL_CampaignHard_Mode = 'normal'


LITERAL_StopCondition_MapAchievement = t.Literal[
    'non_stop', '100_percent_clear', 'map_3_stars', 'threat_safe', 'threat_safe_without_3_stars',
]


class StopCondition(a.GroupBase):
    OilLimit: int = 1000
    RunCount: int = 0
    MapAchievement: LITERAL_StopCondition_MapAchievement = 'non_stop'
    StageIncrease: bool = False
    GetNewShip: bool = False
    ReachLevel: int = 0


LITERAL_Fleet_Fleet = t.Literal[1, 2, 3, 4, 5, 6]
LITERAL_Fleet_Formation = t.Literal['line_ahead', 'double_line', 'diamond']
LITERAL_Fleet_FleetMode = t.Literal['combat_auto', 'combat_manual', 'stand_still_in_the_middle', 'hide_in_bottom_left']
LITERAL_Fleet_FleetStep = t.Literal[2, 3, 4, 5]


class Fleet(a.GroupBase):
    Fleet: LITERAL_Fleet_Fleet = 1
    Formation: LITERAL_Fleet_Formation = 'double_line'
    FleetMode: LITERAL_Fleet_FleetMode = 'combat_auto'
    FleetStep: LITERAL_Fleet_FleetStep = 3


LITERAL_Submarine_Fleet = t.Literal[0, 1, 2]
LITERAL_Submarine_Mode = t.Literal['do_not_use', 'hunt_only', 'boss_only', 'hunt_and_boss', 'every_combat']
LITERAL_Submarine_AutoSearchMode = t.Literal['sub_standby', 'sub_auto_call']
LITERAL_Submarine_DistanceToBoss = t.Literal[
    'to_boss_position', '1_grid_to_boss', '2_grid_to_boss', 'use_open_ocean_support',
]


class Submarine(a.GroupBase):
    Fleet: LITERAL_Submarine_Fleet = 0
    Mode: LITERAL_Submarine_Mode = 'do_not_use'
    AutoSearchMode: LITERAL_Submarine_AutoSearchMode = 'sub_standby'
    DistanceToBoss: LITERAL_Submarine_DistanceToBoss = '2_grid_to_boss'


LITERAL_Emotion_Mode = t.Literal['calculate', 'ignore', 'calculate_ignore']


class Emotion(a.GroupBase):
    Mode: LITERAL_Emotion_Mode = 'calculate'


LITERAL_EmotionRecord_Control = t.Literal[
    'keep_exp_bonus', 'prevent_green_face', 'prevent_yellow_face', 'prevent_red_face',
]
LITERAL_EmotionRecord_Recover = t.Literal['not_in_dormitory', 'dormitory_floor_1', 'dormitory_floor_2']


class EmotionRecord(a.DashboardAmount):
    Value: e.Annotated[int, m.Meta(ge=0, le=150)] = 119
    Control: LITERAL_EmotionRecord_Control = 'prevent_yellow_face'
    Recover: LITERAL_EmotionRecord_Recover = 'not_in_dormitory'
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
        # no updates if edits Recover only
        if 'Value' in edits or 'Time' in edits or 'Recover' in edits or 'Oath' in edits:
            self.update()

    @a.batch_set
    def update(self):
        now = a.getnow()
        recover_count = int(now.timestamp() // 360 - self.Time.timestamp() // 360)
        if recover_count > 0:
            value = min(self.Value + self.speed * recover_count, self.max)
            self.Value = value
        else:
            maximum = self.max
            if self.Value > maximum:
                self.Value = maximum
        self.Time = now


class HpControl(a.GroupBase):
    UseHpBalance: bool = False
    UseEmergencyRepair: bool = False
    UseLowHpRetreat: bool = False
    HpBalanceThreshold: float = 0.2
    HpBalanceWeight: str = '1000, 1000, 1000'
    RepairUseSingleThreshold: float = 0.3
    RepairUseMultiThreshold: float = 0.6
    LowHpRetreatThreshold: float = 0.3


LITERAL_EnemyPriority_EnemyScaleBalanceWeight = t.Literal['default_mode', 'S3_enemy_first', 'S1_enemy_first']


class EnemyPriority(a.GroupBase):
    EnemyScaleBalanceWeight: LITERAL_EnemyPriority_EnemyScaleBalanceWeight = 'default_mode'


LITERAL_GemsCampaign_Name = t.Literal[
    '2-1', '2-2', '2-3', '2-4',
    '3-1', '3-2', '3-3', '3-4',
    '4-1', '4-2', '4-3', '4-4',
    '5-1', '5-2', '5-3', '5-4',
    '6-1', '6-2', '6-3', '6-4',
    '7-1', '7-2', '7-3', '7-4',
    '8-1', '8-2', '8-3', '8-4',
    '9-1', '9-2', '9-3', '9-4',
    '10-1', '10-2', '10-3', '10-4',
    '11-1', '11-2', '11-3', '11-4',
    '12-1', '12-2', '12-3', '12-4',
    '13-1', '13-2', '13-3', '13-4',
    '14-1', '14-2', '14-3', '14-4',
    '15-1', '15-2', '15-3', '15-4',
]


class GemsCampaign(Campaign):
    Name: LITERAL_GemsCampaign_Name = '2-4'
    Mode: LITERAL_CampaignHard_Mode = 'normal'


LITERAL_GemsStopCondition_MapAchievement = t.Literal['non_stop']


class GemsStopCondition(StopCondition):
    RunCount: e.Annotated[int, m.Meta(ge=0, le=999)] = 0
    MapAchievement: LITERAL_GemsStopCondition_MapAchievement = 'non_stop'


LITERAL_GemsSubmarine_Fleet = t.Literal[2]
LITERAL_GemsSubmarine_Mode = t.Literal['hunt_and_boss']


class GemsSubmarine(Submarine):
    Fleet: LITERAL_GemsSubmarine_Fleet = 2
    Mode: LITERAL_GemsSubmarine_Mode = 'hunt_and_boss'


LITERAL_GemsEmotionRecord_Recover = t.Literal['dormitory_floor_2']


class GemsEmotionRecord(EmotionRecord):
    Value: e.Annotated[int, m.Meta(ge=0, le=200)] = 150
    Recover: LITERAL_GemsEmotionRecord_Recover = 'dormitory_floor_2'
