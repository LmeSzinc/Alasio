from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen_index import IndexGenerator
from alasio.ext.path import PathStr
from alasio.logger import logger

if __name__ == '__main__':
    entry = ModEntryInfo('cligen')
    entry.root = PathStr.new(__file__).uppath(3)
    entry.path_config = 'module/config'

    # generate
    logger.info(f'ModEntry: {entry}')
    self = IndexGenerator(entry)
    self.generate()
