import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

LITERAL_DropRecord_API = t.Literal['default', 'cn_gz_reverse_proxy']
LITERAL_DropRecord_ResearchRecord = t.Literal['do_not', 'save', 'upload', 'save_and_upload']
LITERAL_DropRecord_CombatRecord = t.Literal['do_not', 'save']


class DropRecord(a.GroupBase):
    SaveFolder: str = './screenshots'
    AzurStatsID: str = ''
    API: LITERAL_DropRecord_API = 'default'
    ResearchRecord: LITERAL_DropRecord_ResearchRecord = 'do_not'
    CommissionRecord: LITERAL_DropRecord_ResearchRecord = 'do_not'
    CombatRecord: LITERAL_DropRecord_CombatRecord = 'do_not'
    OpsiRecord: LITERAL_DropRecord_ResearchRecord = 'do_not'
    MeowfficerBuy: LITERAL_DropRecord_CombatRecord = 'do_not'
    MeowfficerTalent: LITERAL_DropRecord_ResearchRecord = 'do_not'


LITERAL_Retirement_RetireMode = t.Literal['one_click_retire', 'enhance', 'old_retire']


class Retirement(a.GroupBase):
    RetireMode: LITERAL_Retirement_RetireMode = 'one_click_retire'


LITERAL_OneClickRetire_KeepLimitBreak = t.Literal['keep_limit_break', 'do_not_keep']


class OneClickRetire(a.GroupBase):
    KeepLimitBreak: LITERAL_OneClickRetire_KeepLimitBreak = 'keep_limit_break'


LITERAL_Enhance_ShipToEnhance = t.Literal['all', 'favourite']


class Enhance(a.GroupBase):
    ShipToEnhance: LITERAL_Enhance_ShipToEnhance = 'all'
    Filter: str = ''
    CheckPerCategory: int = 5


LITERAL_OldRetire_RetireAmount = t.Literal['retire_all', 'retire_10']


class OldRetire(a.GroupBase):
    N: bool = True
    R: bool = True
    SR: bool = False
    SSR: bool = False
    RetireAmount: LITERAL_OldRetire_RetireAmount = 'retire_all'
