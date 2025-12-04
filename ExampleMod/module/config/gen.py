from alasio.config_dev.gen_index import IndexGenerator
from alasio.logger import logger
from const import entry

if __name__ == '__main__':
    # generate
    logger.info(f'ModEntry: {entry}')
    self = IndexGenerator(entry)
    self.generate()
