import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

if t.TYPE_CHECKING or 'Campaign' not in globals():
    from ..main.main_model import Campaign, EmotionRecord, StopCondition, Submarine


LITERAL_GemsFarming_ChangeFlagship = t.Literal['ship', 'ship_equip']
LITERAL_GemsFarming_CommonCV = t.Literal['any', 'langley', 'bogue', 'ranger', 'hermes']
LITERAL_GemsFarming_ChangeVanguard = t.Literal['disabled', 'ship', 'ship_equip']
LITERAL_GemsFarming_CommonDD = t.Literal['any', 'favourite', 'aulick_or_foote', 'cassin_or_downes', 'z20_or_z21']


class GemsFarming(a.GroupBase):
    ChangeFlagship: LITERAL_GemsFarming_ChangeFlagship = 'ship'
    CommonCV: LITERAL_GemsFarming_CommonCV = 'any'
    ChangeVanguard: LITERAL_GemsFarming_ChangeVanguard = 'ship'
    CommonDD: LITERAL_GemsFarming_CommonDD = 'any'
    CommissionLimit: bool = True


class EquipmentCode(a.GroupBase):
    ExportToConfig: bool = True
    Config: str = ''


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
LITERAL_GemsCampaign_Mode = t.Literal['normal']


class GemsCampaign(Campaign):
    Name: LITERAL_GemsCampaign_Name = '2-4'
    Mode: LITERAL_GemsCampaign_Mode = 'normal'


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
