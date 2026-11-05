from alasio.config.entry.mod_entry import ModEntry
from alasio.config_dev.gen_index import IndexGenerator
from alasio.config_dev.parse.base import i18n_json_postprocess
from alasio.ext import env
from alasio.ext.deep import deep_get, deep_keys_depth1, deep_set
from alasio.logger import logger


class Generator(IndexGenerator):
    @i18n_json_postprocess('alasio')
    def alas_i18n_json(cls, data):
        # The postprocess is called with the class of its __qualname__ rather
        # than with the instance, so the entry of the generation is read from
        # the class, where CrossNavGenerator publishes it
        languages = cls.entry.gui_language
        # copy scheduler name and help to variants
        for group in [
            'SchedulerStatic',
            'SchedulerEnable',
        ]:
            for lang in languages:
                for prop in ['name', 'help']:
                    value = deep_get(data, keys=['Scheduler', 'Enable', lang, prop], default='')
                    deep_set(data, keys=[group, 'Enable', lang, prop], value=value)
        # SchedulerEnable shares the same options as Scheduler, copy option translations
        for lang in languages:
            value = deep_get(data, keys=['Scheduler', 'Enable', lang, 'option_i18n'], default='')
            deep_set(data, keys=['SchedulerEnable', 'Enable', lang, 'option_i18n'], value=value)
        # SchedulerEnableUedit has an editable ServerUpdate, copy from SchedulerUedit
        for lang in languages:
            for prop in ['name', 'help']:
                value = deep_get(data, keys=['SchedulerUedit', 'ServerUpdate', lang, prop], default='')
                deep_set(data, keys=['SchedulerEnableUedit', 'ServerUpdate', lang, prop], value=value)
        # copy group into to all scheduler
        for group in deep_keys_depth1(data):
            if group == 'Scheduler':
                continue
            for lang in languages:
                for prop in ['name', 'help']:
                    value = deep_get(data, keys=['Scheduler', '_info', lang, prop], default='')
                    deep_set(data, keys=[group, '_info', lang, prop], value=value)
        return data


if __name__ == '__main__':
    env.set_project_root(env.ALASIO_ROOT)
    logger.mute(fd=True)
    # generate
    entry = ModEntry.alasio
    logger.info(f'ModEntry: {entry}')
    self = Generator(entry)
    self.generate()
