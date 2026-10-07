"""
Serve the pack folder of a run directory with hypercorn, for local testing.

The development server of the packs: it serves the whole pack folder of a run
directory, with the names and the layout the packs have on the disk, so a pack
can be fetched with curl or opened in a browser without a deployment. It only
reads the folder, generating the packs is the job of the pack server itself,
see PackRepoGen.

The url mirrors the path under the run directory: the file

    {run directory}/pack/{Author}_{Repo}_{Branch}/packrepo/{version}/full_{version}.pack

is served at

    /pack/{Author}_{Repo}_{Branch}/packrepo/{version}/full_{version}.pack

and latest.pack, an update pack (update_{old_version}.pack) and every other
file of a pack folder are served the same way. The packs of a version are
walked with http range requests, the url layout of the client is in
ServerFile.

The run directory comes from the command line, the packs of every config of
the run directory are served:

    python -m alasio.deploy_dev.pack_server.serve --root D:/AlasPack

    from alasio.deploy_dev.pack_server.serve import create_app, pack_folder
    app = create_app(pack_folder())
"""

import argparse
import functools

import trio
from hypercorn.config import Config
from hypercorn.trio import serve
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from alasio.deploy_dev.pack_server.gate import check_run_dir
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger

# folder of the repos in the run directory, see PackRepoModel
PACK_FOLDER = 'pack'

# folder of the packs of a repo, the subfolder of a repo folder
PACKREPO_FOLDER = 'packrepo'

# url prefix the packs are served under, the folder name
PACK_URL = f'/{PACK_FOLDER}'


def pack_folder():
    """
    Folder of the packs to serve: {run directory}/pack

    Returns:
        PathStr: Absolute path of the folder

    Raises:
        ValueError: If the run directory holds no pack folder
    """
    folder = env.PROJECT_ROOT.joinpath(PACK_FOLDER)
    if not folder.isdir():
        raise ValueError(f'No pack folder in "{env.PROJECT_ROOT}", run the pack server first')
    return folder


def create_app(folder):
    """
    Build the app that serves the packs of a pack folder.

    Args:
        folder (PathStr): Folder of the packs of a run directory, e.g.
            {run directory}/pack, see pack_folder

    Returns:
        Starlette: App of the development server
    """
    folder = PathStr.new(folder)
    # the url mirrors the folder: /pack/{Author}_{Repo}_{Branch}/packrepo/...
    # is {run directory}/pack/{Author}_{Repo}_{Branch}/packrepo/..., the
    # static files of starlette serve the files, their ranges and their 404s
    routes = [Mount(PACK_URL, app=StaticFiles(directory=folder))]
    return Starlette(routes=routes)


def create_config(args=None):
    """
    Parse the command line, build the hypercorn config of the server.

    Args:
        args (list[str] | None): Commandline args, sys.argv[1:] by default

    Returns:
        tuple[Config, PathStr]: Hypercorn config and the pack folder to serve

    Raises:
        SystemExit: If the arguments are invalid
        RunDirError: If env.PROJECT_ROOT is not a run directory of the pack
            server, see check_run_dir
        ValueError: If the run directory holds no pack folder, see pack_folder
    """
    parser = argparse.ArgumentParser(
        description='Serve the pack folder of a run directory, for local testing',
    )
    parser.add_argument(
        '-r', '--root', default='',
        help='run directory of the pack server, default to env.PROJECT_ROOT',
    )
    parser.add_argument(
        '--host', default='127.0.0.1',
        help='bind host, default to 127.0.0.1',
    )
    parser.add_argument(
        '--port', type=int, default=8000,
        help='bind port, default to 8000',
    )
    parsed_args = parser.parse_args(args)
    if parsed_args.root:
        env.set_project_root(parsed_args.root)
    # the project root must be set before the first log: the log file of the
    # process is decided by the first write, see LogWriter.file
    logger.hr('Start', level=0)
    check_run_dir()
    folder = pack_folder()

    config = Config()
    config.bind = [f'{parsed_args.host}:{parsed_args.port}']
    # the requests of a development server are its log
    config.accesslog = '-'
    return config, folder


async def serve_app(args=None):
    """
    Serve the packs of the run directory until the process is stopped.

    Args:
        args (list[str] | None): Commandline args, sys.argv[1:] by default
    """
    config, folder = create_config(args)
    address = config.bind[0]
    logger.info(f'Serving "{folder}" at http://{address}{PACK_URL}/')
    for name in folder.iter_foldernames():
        logger.info(f'http://{address}{PACK_URL}/{name}/{PACKREPO_FOLDER}/latest.pack')
    await serve(create_app(folder), config)


def main():
    """
    Command line entry, serve the pack folder of a run directory.

    Raises:
        SystemExit: If the arguments are invalid
    """
    trio.run(functools.partial(serve_app, args=None))


if __name__ == '__main__':
    main()
