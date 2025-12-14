from ExampleMod.module.config.config_generated import ConfigGenerated


class Config(ConfigGenerated):
    pass


if __name__ == '__main__':
    self = Config('alas')
    self.mod.get_task_schedule(self.config_name)
