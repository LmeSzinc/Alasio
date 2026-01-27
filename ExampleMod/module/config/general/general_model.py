import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class DropRecord(m.Struct, omit_defaults=True):
    SaveFolder: str = './screenshots'
    AzurStatsID: str = ''
    API: t.Literal['default', 'cn_gz_reverse_proxy'] = 'default'
    ResearchRecord: t.Literal['do_not', 'save', 'upload', 'save_and_upload'] = 'do_not'
    CommissionRecord: t.Literal['do_not', 'save', 'upload', 'save_and_upload'] = 'do_not'
    CombatRecord: t.Literal['do_not', 'save'] = 'do_not'
    OpsiRecord: t.Literal['do_not', 'save', 'upload', 'save_and_upload'] = 'do_not'
    MeowfficerBuy: t.Literal['do_not', 'save'] = 'do_not'
    MeowfficerTalent: t.Literal['do_not', 'save', 'upload', 'save_and_upload'] = 'do_not'


class Retirement(m.Struct, omit_defaults=True):
    RetireMode: t.Literal['one_click_retire', 'enhance', 'old_retire'] = 'one_click_retire'


class OneClickRetire(m.Struct, omit_defaults=True):
    KeepLimitBreak: t.Literal['keep_limit_break', 'do_not_keep'] = 'keep_limit_break'


class Enhance(m.Struct, omit_defaults=True):
    ShipToEnhance: t.Literal['all', 'favourite'] = 'all'
    Filter: str = ''
    CheckPerCategory: int = 5


class OldRetire(m.Struct, omit_defaults=True):
    N: bool = True
    R: bool = True
    SR: bool = False
    SSR: bool = False
    RetireAmount: t.Literal['retire_all', 'retire_10'] = 'retire_all'
