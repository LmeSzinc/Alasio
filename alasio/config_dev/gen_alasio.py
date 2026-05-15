from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen_index import IndexGenerator
from alasio.config_dev.parse.base import ParseBase, i18n_json_postprocess
from alasio.ext import env
from alasio.ext.deep import deep_get, deep_keys_depth1, deep_set
from alasio.logger import logger

ParseBase.alasio_nav.clear()


class Generator(IndexGenerator):
    @i18n_json_postprocess('alasio')
    def alas_i18n_json(self, data):
        # copy scheduler name and help to variants
        for group in [
            'SchedulerStatic',
        ]:
            for lang in ModEntryInfo.alasio().gui_language:
                for prop in ['name', 'help']:
                    value = deep_get(data, keys=['Scheduler', 'Enable', lang, prop], default='')
                    deep_set(data, keys=[group, 'Enable', lang, prop], value=value)
        # copy group into to all scheduler
        for group in deep_keys_depth1(data):
            if group == 'Scheduler':
                continue
            for lang in ModEntryInfo.alasio().gui_language:
                for prop in ['name', 'help']:
                    value = deep_get(data, keys=['Scheduler', '_info', lang, prop], default='')
                    deep_set(data, keys=[group, '_info', lang, prop], value=value)
        return data


if __name__ == '__main__':
    env.set_project_root(env.ALASIO_ROOT)
    logger.mute(fd=True)
    # generate
    entry = ModEntryInfo.alasio()
    logger.info(f'ModEntry: {entry}')
    self = IndexGenerator(entry)
    self.generate()
