"""
Startup events of the update manager, standalone so that the sync world
(the worker manager, the restart rpcs) and the async world (the update
manager, the startup orchestration) share them without importing each
other:

- update_inited: set once the update manager finished its initialization
  (every mounted mod has its ModUpdateManager and its first_update_checked
  event): the resume queue of the new backend waits behind it;
- first_update_checked: {mod name: Event}, set when the first update check
  of the mod is over (a check ran, failed, or was skipped: a mod without an
  update source is ready from the start).

A start request landing before the events is accepted as a queued resume by
the worker manager (its gate reads is_set(), a plain attribute check with no
scheduling involved, safe inside the worker lock); the startup orchestration
releases the queue once the events of the mods are set (doc §16.8).
"""
import trio


class UpdateStartup:
    """
    The process-wide startup state of the update manager.

    One instance per process (a trio.Event can only be set once): the module
    singleton UPDATE_STARTUP. reset() drops the events for the tests.
    """

    def __init__(self):
        # set once every mounted mod has its ModUpdateManager
        self.update_inited = trio.Event()
        # {mod name: Event}, set when the first update check of the mod is over
        self.first_update_checked = {}

    def mod_event(self, name):
        """
        The first_update_checked event of a mod, created on first use (the
        ModUpdateManager of the mod registers it at construction).

        Args:
            name (str): Mod name

        Returns:
            trio.Event: The event
        """
        event = self.first_update_checked.get(name, None)
        if event is None:
            event = self.first_update_checked[name] = trio.Event()
        return event

    def is_mod_ready(self, name):
        """
        Whether the mod may start workers right now (the sync-world judge):
        the update manager is initialized and the first update check of the
        mod is over. A mod the update manager never registered is not gated.

        Args:
            name (str): Mod name

        Returns:
            bool: True when the starts of the mod are not gated
        """
        if not self.update_inited.is_set():
            return False
        event = self.first_update_checked.get(name, None)
        return event is None or event.is_set()

    def startup_over(self):
        """
        Whether the startup phase is over: the manager is initialized and
        every registered mod had its first check. The restart orchestration
        refuses the external triggers until this.

        Returns:
            bool: True when the startup phase is over
        """
        if not self.update_inited.is_set():
            return False
        return all(event.is_set() for event in self.first_update_checked.values())

    async def wait_ready(self, names):
        """
        Wait until the manager is initialized and the first update check of
        every given mod is over. The mods the manager never registered are
        skipped: nothing gates their starts.

        Args:
            names (Iterable[str]): Mod names to wait for
        """
        await self.update_inited.wait()
        for name in names:
            event = self.first_update_checked.get(name, None)
            if event is not None:
                await event.wait()

    def release(self):
        """
        Open the gate whatever happened (the update manager stopped or
        failed): refusing every start forever would lock the user out.
        Idempotent.
        """
        self.update_inited.set()
        for event in self.first_update_checked.values():
            event.set()

    def reset(self):
        """
        Drop the events (tests: a fresh process has fresh ones)
        """
        self.__init__()


UPDATE_STARTUP = UpdateStartup()
