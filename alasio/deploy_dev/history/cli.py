"""
Generate the commit history pack from the command line.

    python -m alasio.deploy_dev.history.cli gen

Read the git repository in the current directory and write the
history of the latest 30 commits into .pack/history.pack.

    python -m alasio.deploy_dev.history.cli gen -c /path/to/repo

Use -c to specify the repository directory.
"""
import argparse
import os

from alasio.deploy_dev.history.pack_history import PackHistory
from alasio.ext.path.atomic import atomic_write
from alasio.git.repo import GitRepo


def main():
    """
    Command line entry, generate the commit history pack.

    Raises:
        SystemExit: If the arguments are invalid or the repository
            has no commits
    """
    parser = argparse.ArgumentParser(
        description='Generate the commit history pack of a git repository',
    )
    sub = parser.add_subparsers(dest='command', metavar='command')
    gen = sub.add_parser('gen', help='generate the commit history pack')
    gen.add_argument(
        '-c', '--cwd', default='.',
        help='repository directory, default to the current directory',
    )
    args = parser.parse_args()
    if args.command is None:
        parser.error('missing command, choose from "gen"')

    cwd = args.cwd
    try:
        repo = GitRepo(cwd).read_lazy()
        pack = PackHistory(repo)
    except ValueError as e:
        parser.error(str(e))
    else:
        data = b''.join(pack.iter_commit_history())

        file = os.path.join(cwd, '.pack', 'history.pack')
        atomic_write(file, data)
        print(f'Packed the history of {pack.latest_commit} into {file}, {len(data)} bytes')


if __name__ == '__main__':
    main()
