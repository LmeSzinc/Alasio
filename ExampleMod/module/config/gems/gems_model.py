import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class GemsFarming(a.GroupBase):
    ChangeFlagship: t.Literal['ship', 'ship_equip'] = 'ship'
    CommonCV: t.Literal['any', 'langley', 'bogue', 'ranger', 'hermes'] = 'any'
    ChangeVanguard: t.Literal['disabled', 'ship', 'ship_equip'] = 'ship'
    CommonDD: t.Literal['any', 'favourite', 'aulick_or_foote', 'cassin_or_downes', 'z20_or_z21'] = 'any'
    CommissionLimit: bool = True


class EquipmentCode(a.GroupBase):
    ExportToConfig: bool = True
    Config: str = ''
