class SchedulerTask:
    """
    Task interface of the scheduler.

    A scheduler task is a method that AlasioScheduler calls by name, either
    scheduled from the config, such as "RestartGame", or called internally
    when the task queue is empty, such as "GotoMain".
    The task name is the method name itself, there is no name conversion.

    A MOD must override every scheduler task, otherwise the scheduler raises
    NotImplementedError when running the task. Config gen checks this and
    prints a warning when a scheduler task is not overridden.
    """

    def RestartDevice(self):
        """
        Restart the device, such as killing and re-creating the emulator

        Note:
            RestartDevice is an empty task, it must delay itself at the end
            (config.task_delay(server_update=True)), otherwise the scheduler
            runs it again immediately

        Raises:
            NotImplementedError: If the MOD does not override this scheduler task
        """
        raise NotImplementedError

    def RestartGame(self):
        """
        Restart the game, stop the game first if it is running

        Note:
            RestartGame is an empty task, it must delay itself at the end
            (config.task_delay(server_update=True)), otherwise the scheduler
            runs it again immediately

        Raises:
            NotImplementedError: If the MOD does not override this scheduler task
        """
        raise NotImplementedError

    def StopDevice(self):
        """
        Stop the device, such as stopping the emulator, called during waiting when
        Optimization.WhenTaskQueueEmpty = "stop_device"

        Raises:
            NotImplementedError: If the MOD does not override this scheduler task
        """
        raise NotImplementedError

    def StopGame(self):
        """
        Stop the game, called during waiting when
        Optimization.WhenTaskQueueEmpty = "stop_game"

        Raises:
            NotImplementedError: If the MOD does not override this scheduler task
        """
        raise NotImplementedError

    def GotoMain(self):
        """
        Goto the main page of the game, called during waiting when
        Optimization.WhenTaskQueueEmpty = "goto_main"

        Raises:
            NotImplementedError: If the MOD does not override this scheduler task
        """
        raise NotImplementedError
