from alasio.base.scheduler.scheduler import AlasioScheduler


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class TaskEntryGenerated(AlasioScheduler):
    """
    Task entry functions, generated from @alasio_task() markers
    """

    def GotoMain(self):
        from module.device import Device
        Device(config=self.config, device=self.device).goto_main()

    def RestartDevice(self):
        from module.device import Device
        Device(config=self.config, device=self.device).restart_device()

    def RestartGame(self):
        from module.device import Device
        Device(config=self.config, device=self.device).restart_game()

    def StopDevice(self):
        from module.device import Device
        Device(config=self.config, device=self.device).stop_device()

    def StopGame(self):
        from module.device import Device
        Device(config=self.config, device=self.device).stop_game()

    """
    ========== normal tasks ==========
    """

    def Reward(self):
        from module.reward import Reward
        Reward(config=self.config, device=self.device).run()
