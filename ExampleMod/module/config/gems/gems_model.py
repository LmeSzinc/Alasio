import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

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
