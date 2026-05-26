from alasio.config_dev.gen.gen_cross import CrossNavGenerator
from alasio.ext.cache import cached_property
from alasio.ext.deep import deep_get, deep_set
from alasio.ext.file.msgspecfile import read_msgspec


class GenQueueIndex(CrossNavGenerator):
    """Generator for queue.index.json."""

    @cached_property
    def queue_index_file(self):
        return self.path_config.joinpath('_index/queue.index.json')

    @cached_property
    def queue_index_data(self):
        """
        data of queue.index.json

        Returns:
            dict[str, dict[str, str]]:
                key: {task_name}.{lang}
                value: i18n translation
        """
        old = read_msgspec(self.queue_index_file)
        out = {}

        def iter_tasks_data():
            if self.alasio:
                yield from self.alasio.tasks_data.items()
            yield from self.tasks_data.items()

        for task_name, task_data in iter_tasks_data():
            if not task_data.has_scheduler:
                continue
            for lang in self.entry.gui_language:
                key = [task_name, lang]
                value = deep_get(old, key, default='')
                if not value:
                    value = task_name
                deep_set(out, key, value)
        return out
