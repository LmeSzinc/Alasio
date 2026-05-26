from alasio.config_dev.gen.gen_config_generated import GenConfigGenerated
from alasio.config_dev.gen.gen_config_index import GenConfigIndex
from alasio.config_dev.gen.gen_nav_index import GenNavIndex
from alasio.config_dev.gen.gen_queue_index import GenQueueIndex
from alasio.ext import env
from alasio.ext.file.jsonfile import write_json_custom_indent
from alasio.ext.path import PathStr
from alasio.git.stage.gitadd import GitAdd
from alasio.logger import logger


class IndexGenerator(
    GenConfigIndex,
    GenNavIndex,
    GenQueueIndex,
    GenConfigGenerated,
):
    """
    维护一个MOD下所有设置文件的数据一致性，生成json索引文件。

    1. "tasks.index.json" 指导当前MOD下有哪些task。
        这个文件会在脚本运行时被使用。
        当Alasio脚本实例启动时，它需要读取与某个task相关的设置：
        - 在"model.index.json"中查询当前task
        - 遍历task下有哪些group，得到group所在的{nav}_model.py文件路径
        - 载入python文件，查询{nav}_model.py中的group对应的msgspec模型
        - 读取用户数据，使用msgspec模型校验数据

    2. "config.index.json" 指导当前MOD有哪些设置，具有 nav 一级结构。
        这个文件会在前端显示时被使用。
        当前端需要显示某个nav下的用户设置时：
        - 按file指示，加载{nav}_config.json作为结构
        - 按i18n指示，加载{nav}_i18n.json
        - 按config指示，加载用户设置中的task和group
        - 聚合所有内容

    3. "nav.index.json" 是任务和任务导航的i18n，具有 nav_name.card_name.lang 三级结构。
        这个文件会在前端显示时被使用。
        注意这是一个半自动生成文件，Alasio会维护它的数据结构，但是需要人工编辑nav对应的i18n，
        当前端需要显示导航组件时：
        - 读取"nav.index.json"返回给前端
    """

    """
    Generate all
    """

    def _generate(self, gitadd=None):
        # check path
        if not self.root.exists():
            logger.warning(f'ConfigGen root not exist: {self.root}')
        if not self.path_config.exists():
            logger.warning(f'ConfigGen path_config not exist: {self.path_config}')

        self.build_group_mro()

        # update nav configs
        for nav in self.dict_nav_config.values():
            nav.generate(gitadd=gitadd)

        # model.index.json
        _ = self.model_data
        if not self.model_data:
            return

        # {nav}_config.json
        self.generate_config_json(gitadd=gitadd)

        def write(f: PathStr, d):
            if d:
                op = write_json_custom_indent(f, d, skip_same=True)
                if op:
                    logger.info(f'Write file {f}')
                    if gitadd:
                        gitadd.stage_add(f)
            else:
                if f.exists():
                    logger.info(f'Delete file {f}')
                    f.atomic_remove()
                    if gitadd:
                        gitadd.stage_add(f)

        # task.index.json
        write(self.task_index_file, self.task_index_data)

        # config.index.json
        write(self.config_index_file, self.config_index_data)

        # nav.index.json
        write(self.nav_index_file, self.nav_index_data)

        # queue.index.json
        write(self.queue_index_file, self.queue_index_data)

        # config_generated.py
        self.generate_config_generated_file(gitadd=gitadd)

        # generate alasio/config/alasio/group_export.py
        if not self.alasio:
            self.generate_group_export(gitadd=gitadd)

    def generate(self):
        with GitAdd(env.PROJECT_ROOT) as gitadd:
            self._generate(gitadd)
