import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class Game(m.Struct, omit_defaults=True):
    PackageName: t.Literal['auto'] = 'auto'
    ServerName: t.Literal['disabled'] = 'disabled'
