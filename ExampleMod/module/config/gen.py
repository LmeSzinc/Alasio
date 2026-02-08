from ExampleMod.module.config.const import entry
from alasio.config_dev.gen_index import IndexGenerator
from alasio.ext.env import set_project_root
from alasio.logger import logger

if __name__ == '__main__':
    set_project_root(__file__, up=4)
    # generate
    logger.info(f'ModEntry: {entry}')
    self = IndexGenerator(entry)
    self.generate()
