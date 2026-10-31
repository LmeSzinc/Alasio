"""
Warmup of a mod: its model files and its json.

The backend serves every mod from one process, and the first request to a mod
pays the file io and the decode of everything it needs. The warmup does that
work in the background at startup, so the request path starts warm, and lets a
request that arrives too early wait for its own mod instead of paying the same
work a second time in its own thread.
"""
import time

import trio

from alasio.ext.cache import cached_property
from alasio.logger import logger


class ModPreloadJob:
    """
    The warmup of one mod: two threads, one event.

    The models and the json do not depend on each other, so they load at the
    same time; the event is set when both ended, whatever their outcome.
    """

    def __init__(self, mod):
        """
        Args:
            mod (Mod): Mod to warm up
        """
        self.mod = mod
        # set by the loop thread once both phases ended, see ModPreload.run
        self.done = trio.Event()

    async def run(self):
        """
        [trio] Load the models and the json of the mod

        Returns:
            tuple[int, int, int]: Packed json files, loaded model files, and
                the wall clock of the warmup in milliseconds
        """
        counts = {}
        start = time.perf_counter()
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self._run_phase, 'models', counts, self.mod.preload_models)
            nursery.start_soon(self._run_phase, 'json', counts, self.mod.preload_json)
        elapsed = (time.perf_counter() - start) * 1000
        return counts.get('json', 0), counts.get('models', 0), elapsed

    async def _run_phase(self, name, counts, func):
        """
        Run one phase in a thread of the trio pool.

        A phase that fails must not cancel the other one: the warmup is an
        optimization, the request path loads what is missing by itself.

        Args:
            name (str): Phase name, for the log
            counts (dict[str, int]): Output, the file count of the phase
            func (callable): Synchronous warmup of the phase
        """
        try:
            counts[name] = await trio.to_thread.run_sync(func)
        except Exception as e:
            logger.error(f'Preload {name} of mod "{self.mod.name}" failed: {e}')
            logger.exception(e)


class ModPreload:
    """
    Warm the models and the json of every mod of the backend.

    One mod at a time with two threads: the decode is cpu work under the GIL
    (a 11 nav mod measured 11 ms in one mod's two threads, and the same wall
    clock for 2, 4 or 8 threads), and a sequence keeps the number of threads
    the backend borrows bounded, whatever the number of mods.

    The events live here, not on the Mod: they belong to the loop thread, and
    a Mod is also created in a worker process, which has no trio loop.

    The jobs are built from the loader on first use. The lifespan warms the
    loader before the warmup is scheduled, so building them is a dict read,
    and a request that arrives before `run()` reached its mod builds the very
    same events and waits on them instead of loading in its own thread.
    """

    @cached_property
    def jobs(self):
        """
        Job of every mod of the backend

        A worker process builds no job: it serves no topic, and it has no
        warmup to wait for.

        Returns:
            dict[str, ModPreloadJob]: Job of each mod, by mod name
        """
        # local import: the loader binds itself to the project root at import
        # time, which create_config sets before the app is built
        from alasio.config.entry.loader import MOD_LOADER

        return {name: ModPreloadJob(mod) for name, mod in MOD_LOADER.dict_mod.items()}

    async def run(self):
        """
        [trio] Warm every mod, one mod after the other
        """
        for job in self.jobs.values():
            try:
                json_files, model_files, elapsed = await job.run()
            except Exception as e:
                # defensive: job.run() reports the phases itself
                logger.error(f'Preload of mod "{job.mod.name}" failed: {e}')
                logger.exception(e)
            else:
                logger.info(f'Mod "{job.mod.name}" warmed up: '
                            f'{model_files} model files, {json_files} json files, {elapsed:.1f} ms')
            finally:
                job.done.set()

    async def wait(self, mod_name):
        """
        [trio] Wait until the models and the json of the mod are ready

        Args:
            mod_name (str): Mod name, empty when no mod is selected
        """
        try:
            job = self.jobs[mod_name]
        except KeyError:
            # no such mod, or this process does not warm up (a worker imports
            # the game script, which imports the models as packages)
            return
        await job.done.wait()


MOD_PRELOAD = ModPreload()
