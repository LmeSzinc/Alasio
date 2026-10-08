from msgspec import Struct

from alasio.backend.app.lifespan import lifespan_restart
from alasio.backend.locale.accept_language import negotiate_accept_language
from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import RpcValueError
from alasio.backend.reactive.rx_trio import async_reactive, async_reactive_source
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.ws.ws_topic import BaseTopic
from alasio.config.const import Const
from alasio.config.table.scan import validate_config_name


class NavState(Struct):
    lang: str = 'en-US'
    config_name: str = ''
    mod_name: str = ''
    nav_name: str = ''

    def clear(self):
        self.config_name = ''
        self.mod_name = ''
        self.nav_name = ''


class ConnState(BaseTopic):
    TOPIC_NAME = 'ConnState'

    @async_reactive_source
    async def nav_state(self):
        return NavState()

    # Proxy reactive nav_state,
    # so one modification to nav_state won't trigger mutation to all topics listening to nav_state
    @async_reactive
    async def lang(self) -> str:
        state = await self.nav_state
        return state.lang

    @async_reactive
    async def config_name(self) -> str:
        state = await self.nav_state
        return state.config_name

    @async_reactive
    async def mod_name(self) -> str:
        state = await self.nav_state
        return state.mod_name

    @async_reactive
    async def nav_name(self) -> str:
        state = await self.nav_state
        return state.nav_name

    @rpc
    async def set_lang(self, lang: str):
        use = negotiate_accept_language(lang, Const.GUI_LANGUAGE)
        if not use:
            raise RpcValueError(f'Language "{lang}" does not match any available languages')
        # set
        state: NavState = await self.nav_state
        state.lang = lang
        await self.nav_state.mutate()

    @rpc
    async def restart(self):
        """
        Gracefully restart the entire backend

        Every running worker is asked to stop gracefully (scheduler-stopping:
        the current task finishes first, a 10-minute wait escalates to a kill),
        then the backend restarts and resumes the workers stopped by this
        request. Returns as soon as the restart is accepted: the progress flows
        through the Restart and Worker topics.
        """
        # local import: topic.log imports ConnState from this module, a module
        # level import of restart (-> topic._worker -> topic.log) would be
        # circular
        from alasio.backend.app import restart

        try:
            await restart.request_graceful_restart('user request')
        except restart.RestartInProgress:
            # keep the historical user-facing wording of this rpc
            raise RpcValueError('Restart already in progress') from None
        except restart.RestartUnavailable as e:
            # an update transaction / the startup window owns the backend: the
            # reason carries the next step for the user (doc §16.3)
            raise RpcValueError(str(e)) from None

    @rpc
    async def cancel_restart(self):
        """
        Cancel the graceful restart waiting for the workers to stop

        Only the wait is cancellable: once every worker stopped, the resume
        list is frozen and the backend restarts whatever happens. Nothing is
        resumed after a cancel -- the backend keeps running, the configs it
        stopped stay stopped (start them again manually) and a config still
        finishing its task only loses the restart mark (the scheduler can
        continue it).

        The restart of an update transaction in its applying phase is refused
        (the update is not cancellable past its download phase, §2.5): the
        stop of the workers there is the update, not a plain restart.
        """
        # local import (see restart above)
        from alasio.backend.app import restart
        from alasio.backend.app.update import UPDATE_MANAGER

        if UPDATE_MANAGER.applying:
            raise RpcValueError('The update is being applied and cannot be cancelled')
        # running is the flag of the restart of this backend: the auto-resume
        # queue of the new backend (the other entry point of the cancel) is not
        # a restart in progress and must not be dropped by this rpc
        if not restart.GRACEFUL_RESTART.running:
            raise RpcValueError('No restart in progress')
        await restart.cancel_graceful_restart('user cancel')

    @rpc
    async def force_restart(self):
        """
        Restart the entire backend immediately

        Unlike restart(), the workers are not waited for: every running worker
        is ended right away and nothing is resumed after the backend restarted.
        """
        # local import (see restart above)
        from alasio.backend.app import restart

        # a graceful restart in progress (or a resume queue of the previous
        # one) is cancelled first: no worker of it may be resumed
        await restart.cancel_graceful_restart('force restart')
        await lifespan_restart()

    @rpc
    async def set_config(self, name: str):
        """
        Set config name, and change nav_state accordingly
        """
        # allow set empty config to clear state on page leave
        if not name:
            state: NavState = await self.nav_state
            state.clear()
            # broadcast
            await self.nav_state.mutate()
            return

        # check if name is a validate filename
        error = validate_config_name(name)
        if error:
            raise RpcValueError(error)

        state: NavState = await self.nav_state
        if state.config_name == name:
            # same config, no need to change it
            return

        # get current configs
        data = ConfigScanSource().data
        try:
            config = data[name]
        except KeyError:
            raise RpcValueError(f'No such config: "{name}"')

        # set
        # note that mod_name is calculated in backend to ensure consistency of mod_name and config_name
        state.config_name = name
        state.mod_name = config.mod
        # reset nav when switching to new config
        state.nav_name = ''

        # broadcast
        await self.nav_state.mutate()

    @rpc
    async def set_nav(self, name: str):
        """
        Set config name, and change nav_state accordingly
        """
        state: NavState = await self.nav_state
        if state.nav_name == name:
            # same nav, no need to change it
            return

        # maybe we don't need to validate nav_name, as ConfigArg can handle
        state.nav_name = name

        # broadcast
        await self.nav_state.mutate()
