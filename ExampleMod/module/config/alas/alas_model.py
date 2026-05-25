import datetime as d
import typing as t

import alasio.config.group_base as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class Game(a.GroupBase):
    PackageName: t.Literal['auto'] = 'auto'
    ServerName: t.Literal['disabled'] = 'disabled'
