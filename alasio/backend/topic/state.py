from msgspec import Struct

from alasio.backend.app.lifespan import lifespan_restart
from alasio.backend.locale.accept_language import negotiate_accept_language
from alasio.backend.mpipe.mpipe_backend import mpipe_backend
from alasio.backend.reactive.base_rpc import rpc
from alasio.backend.reactive.event import RpcValueError
from alasio.backend.reactive.rx_trio import async_reactive, async_reactive_source
from alasio.backend.topic.scan import ConfigScanSource
from alasio.backend.ws.context import GLOBAL_CONTEXT
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
        from alasio.backend.app.restart import GRACEFUL_RESTART, run_graceful_restart

        if not mpipe_backend:
            raise PermissionError('Cannot restart backend running without supervisor')
        if GRACEFUL_RESTART.running:
            raise RpcValueError('Restart already in progress')
        # set the flag synchronously (no await in between): two concurrent
        # clicks cannot start two orchestrations
        GRACEFUL_RESTART.running = True
        GLOBAL_CONTEXT.global_nursery.start_soon(run_graceful_restart)

    @rpc
    async def force_restart(self):
        """
        Restart the entire backend immediately

        Unlike restart(), the workers are not waited for: every running worker
        is ended right away and nothing is resumed after the backend restarted.
        """
        # local import (see restart above)
        from alasio.backend.app.restart import cancel_graceful_restart

        # a graceful restart in progress (or a resume queue of the previous
        # one) is cancelled first: no worker of it may be resumed
        await cancel_graceful_restart('force restart')
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
