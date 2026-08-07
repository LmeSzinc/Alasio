"""
Unpack a full pack from the command line.

    python -m alasio.deploy.unpack xxx.pack

The full pack is unpacked into the current working directory, the
pack structure (.pack/) is created inside it.
"""
import argparse
import os

from alasio.deploy.pack.job import DeployJob
from alasio.ext import env
from alasio.ext.path.atomic import atomic_read_bytes


def main():
    """
    Command line entry, unpack a full pack into the current directory.

    Raises:
        SystemExit: If the arguments are invalid or the pack file
            cannot be read
    """
    parser = argparse.ArgumentParser(
        description='Unpack a full pack into the current working directory',
    )
    parser.add_argument('pack', help='Full pack file to unpack')
    args = parser.parse_args()

    # unpack into the current working directory
    env.set_project_root(os.getcwd())
    try:
        data = atomic_read_bytes(args.pack)
    except FileNotFoundError as e:
        parser.error(f'failed to read the pack file: {e}')
    else:
        DeployJob.unpack(data)


if __name__ == '__main__':
    main()
