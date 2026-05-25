import typing as t

from alasio.config.base import AlasioConfigBase

if t.TYPE_CHECKING:
    from .alasio import alasio_model as alasio
    from .alasio import device_model as device


class AlasioConfigGenerated(AlasioConfigBase):
    # A generated config struct to fool IDE's type-predict and auto-complete

    """
    ========== nav: alasio ==========
    """

    """
    ========== nav: device ==========
    """
    # ----- Device -----
    Emulator: "device.Emulator"
    EmulatorInfo: "device.EmulatorInfo"
    Error: "device.Error"
    Optimization: "device.Optimization"

    # ----- RestartDevice -----
    Scheduler: "alasio.Scheduler"

    # ----- RestartGame -----
    # Scheduler: "alasio.Scheduler"

    """
    ========== nav: store ==========
    """
