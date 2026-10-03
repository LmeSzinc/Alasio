"""
Generate the packs of every config in the run directory.

The modules below do one step of the work each, this module chains them:

0. PackRepoConfig.write_template() writes config/template.yaml from the
   model: the operator copies it to a config file, e.g.
   config/LmeSzinc_AzurLaneAutoScript_master.yaml, and fills the values
1. PackRepoConfig reads config/{Author}_{Repo}_{Branch}.yaml from the run
   directory, the file is created from the model when it does not exist
2. PackRepo clones or fetches the git repo of the config into
   repo/{Author}_{Repo}
3. PackRepoGen generates the packs of the repo into
   pack/{Author}_{Repo}_{Branch}

The configs are run one by one, sorted by name. A config that fails is
logged with its traceback and left out, the other configs still run: a
broken config, a remote that cannot be reached or a repo that does not
build must not stop the whole run. The run is logged at the end, and a
PackRunError is raised when at least one config failed, so the scheduler
that starts the pack server sees the failed run and can retry it.

Usage:
    python -m alasio.deploy_dev.pack_server.main --root D:/AlasPack

    from alasio.deploy_dev.pack_server.main import PackServer
    PackServer().run()
"""

import argparse

from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.deploy_dev.pack_server.model import PackRepoConfig
from alasio.deploy_dev.pack_server.pack_gen import PackRepoGen
from alasio.deploy_dev.pack_server.pack_repo import PackRepo
from alasio.ext import env
from alasio.logger import logger


class PackRunError(ValueError):
    """
    Raised when the run of at least one config failed

    Attributes:
        errors (list[tuple[str, Exception]]): (config file, error) of every
            failed config, in the order of the run
    """

    def __init__(self, errors):
        """
        Args:
            errors (list[tuple[str, Exception]]): (config file, error) of
                every failed config
        """
        self.errors = errors
        count = len(errors)
        names = ', '.join(name for name, _ in errors)
        plural = 's' if count > 1 else ''
        super().__init__(f'Failed to run {count} config{plural}: {names}')


class PackServer:
    """
    Generate the packs of every config of the run directory, see the module docstring.

    Usage:
        PackServer().run()
    """

    def iter_config(self):
        """
        Iter the config files of the run directory.

        The config files are the *.yaml files of the config folder of the run
        directory, sorted by name so a run follows the same order every time.
        The template of a config file is skipped, it is the file the operator
        copies and fills, see PackRepoConfig.write_template. A folder that
        does not exist has no config, see run(). A file that is not a *.yaml
        file is not a config either, e.g. the tmp file of an interrupted
        write (file.yaml.xxxxxxxx.tmp), an incomplete config is never run.

        Yields:
            str: File name of a config in the config folder, e.g.
                'LmeSzinc_AzurLaneAutoScript_master.yaml'
        """
        folder = env.PROJECT_ROOT.joinpath(PackRepoConfig.CONFIG_FOLDER)
        for name in sorted(folder.iter_filenames(ext='.yaml')):
            if name == PackRepoConfig.TEMPLATE_FILE:
                # the template is not a config: the operator copies it to a
                # config file and fills the values
                continue
            yield name

    def run_config(self, file):
        """
        Run the flow of one config: read, clone or fetch, generate.

        Args:
            file (str): Name of the config file in the config folder

        Raises:
            RunDirError: If env.PROJECT_ROOT is not a run directory of the
                pack server, see check_run_dir
            ValueError: If Author, Repo, Remote or Branch of the config is
                empty, or the repo of the config cannot be packed
            CmdlineError: If a git command of the repo fails
            OSError: If a pack of the repo cannot be written
        """
        config = PackRepoConfig(file)
        repo = PackRepo(config.data).run()
        PackRepoGen(repo, config.data).run()

    def run(self):
        """
        Generate the packs of every config of the run directory.

        The configs are run one by one, a config that fails is logged with
        its traceback and does not stop the other ones, see the module
        docstring. The result of the run is logged at the end.

        Raises:
            RunDirError: If env.PROJECT_ROOT is not a run directory of the
                pack server, see check_run_dir
            PackRunError: If at least one config failed
        """
        check_run_dir()
        # the template of a config file is written first, so the operator has
        # a file to copy even on a run directory that has no config yet
        PackRepoConfig.write_template()
        file_list = list(self.iter_config())
        if not file_list:
            folder = env.PROJECT_ROOT.joinpath(PackRepoConfig.CONFIG_FOLDER)
            logger.warning(
                f'No config file in "{folder}", '
                f'copy "{PackRepoConfig.TEMPLATE_FILE}" to a config file and fill it')
            return

        plural = 's' if len(file_list) > 1 else ''
        logger.info(f'Running {len(file_list)} config{plural} of "{env.PROJECT_ROOT}"')
        errors = []
        for file in file_list:
            logger.hr(f'Running config "{file}"', level=1)
            try:
                self.run_config(file)
            except Exception as e:
                # one config must not stop the others: a broken config or a
                # remote that cannot be reached is retried by the next run,
                # the error is collected and raised at the end of the run
                errors.append((file, e))
                logger.exception(f'Config "{file}" failed')
            else:
                logger.info(f'Config "{file}" done')

        done = len(file_list) - len(errors)
        logger.info(f'Ran {len(file_list)} config{plural}: {done} done, {len(errors)} failed')
        if errors:
            raise PackRunError(errors)


def main():
    """
    Command line entry, generate the packs of every config of the run directory.

    Raises:
        SystemExit: If the arguments are invalid
    """
    logger.hr('Start', level=0)
    parser = argparse.ArgumentParser(
        description='Generate the packs of every config of the pack server',
    )
    parser.add_argument(
        '-r', '--root', default='',
        help='run directory of the pack server, default to env.PROJECT_ROOT',
    )
    args = parser.parse_args()
    if args.root:
        env.set_project_root(args.root)
    PackServer().run()


if __name__ == '__main__':
    main()
