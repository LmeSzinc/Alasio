import typing as t

if t.TYPE_CHECKING:
    from ..alasio import alasio_model as alasio
    from ..alasio import device_model as device


class AlasioConfigGenerated:
    # A generated config struct to fool IDE's type-predict and auto-complete

    """
    ========== nav: alasio ==========
    """
    Scheduler: "alasio.Scheduler"

    """
    ========== nav: device ==========
    """
    # ----- Device -----
    Emulator: "device.Emulator"
    EmulatorInfo: "device.EmulatorInfo"
    Error: "device.Error"
    Optimization: "device.Optimization"

    # ----- RestartDevice -----
    # Scheduler: "alasio.SchedulerStatic"

    # ----- RestartGame -----
    # Scheduler: "alasio.SchedulerStatic"

    """
    ========== nav: store ==========
    """
