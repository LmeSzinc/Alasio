import typing

from alasio.config.base import AlasioConfigBase

if typing.TYPE_CHECKING:
    from .alasio import alasio_model as alasio
    from .alasio import device_model as device


class ConfigGenerated(AlasioConfigBase):
    # A generated config struct to fool IDE's type-predict and auto-complete

    # alasio
    Scheduler: "alasio.Scheduler"

    # device
    Emulator: "device.Emulator"
    EmulatorInfo: "device.EmulatorInfo"
    Error: "device.Error"
    Optimization: "device.Optimization"
