from copy import deepcopy

from ExampleMod.module.config.const import entry
from alasio.config_dev.gen_index import IndexGenerator
from alasio.config_dev.parse.base import args_yaml_postprocess
from alasio.ext.deep import deep_set
from alasio.ext.env import set_project_root
from alasio.logger import logger

CHAPTER_MAP = {
    'chapter1': ['1-1', '1-2', '1-3', '1-4'],
    'chapter2': ['2-1', '2-2', '2-3', '2-4'],
    'chapter3': ['3-1', '3-2', '3-3', '3-4'],
    'chapter4': ['4-1', '4-2', '4-3', '4-4'],
    'chapter5': ['5-1', '5-2', '5-3', '5-4'],
    'chapter6': ['6-1', '6-2', '6-3', '6-4'],
    'chapter7': ['7-1', '7-2', '7-3', '7-4'],
    'chapter8': ['8-1', '8-2', '8-3', '8-4'],
    'chapter9': ['9-1', '9-2', '9-3', '9-4'],
    'chapter10': ['10-1', '10-2', '10-3', '10-4'],
    'chapter11': ['11-1', '11-2', '11-3', '11-4'],
    'chapter12': ['12-1', '12-2', '12-3', '12-4'],
    'chapter13': ['13-1', '13-2', '13-3', '13-4'],
    'chapter14': ['14-1', '14-2', '14-3', '14-4'],
    'chapter15': ['15-1', '15-2', '15-3', '15-4'],
}


class Generator(IndexGenerator):
    @args_yaml_postprocess('main')
    def main_args_json(cls, data):
        option = deepcopy(CHAPTER_MAP)
        data = deep_set(data, keys='Campaign.args.Name.option', value=option)
        return data

    @args_yaml_postprocess('gems')
    def gems_args_json(cls, data):
        option = deepcopy(CHAPTER_MAP)
        option.pop('chapter1', None)
        data = deep_set(data, keys='GemsCampaign.args.Name.option', value=option)
        return data


if __name__ == '__main__':
    set_project_root(__file__, up=4)
    logger.mute(fd=True)
    # generate
    logger.info(f'ModEntry: {entry}')
    self = Generator(entry)
    self.generate()
