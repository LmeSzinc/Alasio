from alasio.config_dev.format.format_yaml import yaml_formatter
from alasio.config_dev.gen.gen_config import ConfigGenerator
from alasio.config_dev.parse.base import DefinitionError
from alasio.ext.file.jsonfile import write_json_custom_indent
from alasio.ext.file.yamlfile import format_yaml, read_yaml
from alasio.logger import logger


class ParseStore(ConfigGenerator):
    """
    A global dashboard base data.
    Any dashboard groups will inherit from these base groups
    """

    def read_args_yaml(self):
        # Bypass plugin postprocess
        data = read_yaml(self.file)
        if not data:
            raise DefinitionError('Empty dashboard.args.yaml', file=self.file)
        return data

    def generate(self, gitadd=None):
        # generate i18n only, dashboard_model.py is manual maintained
        # {nav}_i18n.json
        if self.i18n_data:
            file = self.i18n_file
            op = write_json_custom_indent(file, self.i18n_data, skip_same=True)
            if op:
                logger.info(f'Write file {file}')
                if gitadd:
                    gitadd.stage_add(file)

        # format yaml
        op = format_yaml(self.file, yaml_formatter)
        if op:
            logger.info(f'Write file {self.file}')
            if gitadd:
                gitadd.stage_add(self.file)
