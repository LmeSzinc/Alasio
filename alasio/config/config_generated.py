import typing as t

from alasio.config.base import AlasioConfigBase

if t.TYPE_CHECKING:
    from .alasio import alasio_model as alasio
    from .alasio import device_model as device
    from .alasio import store_model as store


class AlasioConfigGenerated(AlasioConfigBase):
    # A generated config struct to fool IDE's type-predict and auto-complete

    # alasio

    # device
    Emulator: "device.Emulator"
    EmulatorInfo: "device.EmulatorInfo"
    Error: "device.Error"
    Optimization: "device.Optimization"

    # store
