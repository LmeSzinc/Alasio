import datetime as d
import typing as t

import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config.dev.configgen ```

class Emulator(m.Struct, omit_defaults=True):
    Serial: str = 'auto'
    ScreenshotMethod: t.Literal[
        'auto',
        'ADB',
        'ADB_nc',
        'uiautomator2',
        'aScreenCap',
        'aScreenCap_nc',
        'DroidCast',
        'DroidCast_raw',
        'nemu_ipc',
        'ldopengl',
    ] = 'auto'
    ControlMethod: t.Literal[
        'ADB',
        'uiautomator2',
        'minitouch',
        'Hermit',
        'MaaTouch',
    ] = 'MaaTouch'
    ScreenshotDedithering: bool = False
    AdbRestart: bool = False


class EmulatorInfo(m.Struct, omit_defaults=True):
    Emulator: t.Literal[
        'auto',
        'NoxPlayer',
        'NoxPlayer64',
        'BlueStacks4',
        'BlueStacks5',
        'BlueStacks4HyperV',
        'BlueStacks5HyperV',
        'LDPlayer3',
        'LDPlayer4',
        'LDPlayer9',
        'MuMuPlayer',
        'MuMuPlayerX',
        'MuMuPlayer12',
        'MEmuPlayer',
    ] = 'auto'
    Name: str = ''
    Path: str = ''


class Error(m.Struct, omit_defaults=True):
    HandleError: bool = True
    SaveError: bool = True
    OnePushConfig: str = 'provider: null'
    ScreenshotLength: int = 1


class Optimization(m.Struct, omit_defaults=True):
    ScreenshotInterval: float = 0.3
    CombatScreenshotInterval: float = 1.0
    TaskHoardingDuration: int = 0
    WhenTaskQueueEmpty: t.Literal['stay_there', 'goto_main', 'stop_game'] = 'goto_main'
