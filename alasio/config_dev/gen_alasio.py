from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen_index import IndexGenerator
from alasio.config_dev.parse.base import ParseBase, i18n_json_postprocess
from alasio.ext import env
from alasio.ext.deep import deep_get, deep_set

ParseBase.alasio_nav.clear()


class Generator(IndexGenerator):
    @i18n_json_postprocess('alasio')
    def alas_i18n_json(self, data):
        # copy scheduler name and help to variants
        for group in [
            'SchedulerU00',
            'SchedulerStaticU00',
            'SchedulerU04',
            'SchedulerStaticU04',
        ]:
            for lang in ModEntryInfo.alasio().gui_language:
                for prop in ['name', 'help']:
                    value = deep_get(data, keys=['Scheduler', 'Enable', lang, prop], default='')
                    deep_set(data, keys=[group, 'Enable', lang, prop], value=value)
        # copy scheduler static to variants
        for group in [
            'SchedulerStaticU00',
            'SchedulerStaticU04',
        ]:
            for lang in ModEntryInfo.alasio().gui_language:
                value = deep_get(data, keys=['SchedulerStatic', 'Enable', lang, 'option_i18n', 'enabled'], default='')
                deep_set(data, keys=[group, 'Enable', lang, 'option_i18n', 'enabled'], value=value)
        return data


if __name__ == '__main__':
    env.set_project_root(env.ALASIO_ROOT)
    alasio = ModEntryInfo.alasio()
    self = IndexGenerator(alasio)
    self.generate()
