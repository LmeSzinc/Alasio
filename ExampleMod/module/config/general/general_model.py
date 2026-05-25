import datetime as d
import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class DropRecord(a.GroupBase):
    SaveFolder: str = './screenshots'
    AzurStatsID: str = ''
    API: t.Literal['default', 'cn_gz_reverse_proxy'] = 'default'
    ResearchRecord: t.Literal['do_not', 'save', 'upload', 'save_and_upload'] = 'do_not'
    CommissionRecord: t.Literal['do_not', 'save', 'upload', 'save_and_upload'] = 'do_not'
    CombatRecord: t.Literal['do_not', 'save'] = 'do_not'
    OpsiRecord: t.Literal['do_not', 'save', 'upload', 'save_and_upload'] = 'do_not'
    MeowfficerBuy: t.Literal['do_not', 'save'] = 'do_not'
    MeowfficerTalent: t.Literal['do_not', 'save', 'upload', 'save_and_upload'] = 'do_not'


class Retirement(a.GroupBase):
    RetireMode: t.Literal['one_click_retire', 'enhance', 'old_retire'] = 'one_click_retire'


class OneClickRetire(a.GroupBase):
    KeepLimitBreak: t.Literal['keep_limit_break', 'do_not_keep'] = 'keep_limit_break'


class Enhance(a.GroupBase):
    ShipToEnhance: t.Literal['all', 'favourite'] = 'all'
    Filter: str = ''
    CheckPerCategory: int = 5


class OldRetire(a.GroupBase):
    N: bool = True
    R: bool = True
    SR: bool = False
    SSR: bool = False
    RetireAmount: t.Literal['retire_all', 'retire_10'] = 'retire_all'
