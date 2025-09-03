import argparse

from alasio.config.entry.const import ModEntryInfo
from alasio.config_dev.gen_index import IndexGenerator
from alasio.ext.path.calc import abspath, is_abspath
from alasio.logger import logger

if __name__ == '__main__':
    """Main function to parse arguments and run the generator."""
    parser = argparse.ArgumentParser(
        description="Generate indexes for a mod. Creates a new ModEntryInfo based on command-line arguments.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        '--name',
        type=str,
        default=None,
        help="mod name"
    )
    parser.add_argument(
        '--root',
        type=str,
        default=None,
        help='absolute path to mod root folder, or relative path from running folder to mod root folder, '
             'default to ""',
    )
    parser.add_argument(
        '--path-config',
        type=str,
        default=None,
        help='relative path from mod root to config folder, '
             'default to "module/config"',
    )
    args = parser.parse_args()

    # create temp mod entry
    entry = ModEntryInfo('cligen')
    if args.root:
        entry.root = args.root
    if not is_abspath(entry.root):
        entry.root = abspath(entry.root)
    if args.path_config:
        entry.path_config = args.path_config

    # generate
    logger.info(f'ModEntry: {entry}')
    self = IndexGenerator(entry)
    self.generate()
