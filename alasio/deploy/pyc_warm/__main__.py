"""
Warm the bytecode cache of the environment and the mods, the entry of the
warmup subprocess.

    python -m alasio.deploy.pyc_warm [--root DIR] [--json] [--dry-run]

The subprocess runs the interpreter of the release, so the cache tag, the
magic number and the optimization level of the pyc files it writes are the
ones of the worker that imports them afterwards. The chain shares one set
of flags: the backend and the worker are spawn children of the supervisor,
and a spawn child keeps the optimization level of its parent, so a warmup
started by the backend writes the pyc the worker reads whatever -O says.
A run started by hand needs the interpreter of the release for the same
reason. It may be started with -B, py_compile writes the files regardless,
but the sources the run imports itself, e.g. the scope reader, are then
compiled every time.

The scope is the environment of the running interpreter and the mods of
the project root: --root sets the project root, which is where the mods of
the release declare themselves, see Scope.mods. Nothing is guessed from
the layout of a release.

The output is meant for the parent process. The report is logged rather
than printed, and a run started by the backend passes --json, where the
logger is muted on all of its outputs: stdout then carries the JSON line
alone, and the warmup never opens the log file of the release, which the
backend is starting at that same moment. A run started by hand keeps its
lines. The exit code only tells whether the run could start, a source
that fails to compile is counted, never raised: the warmup is an
optimization, and the parent must never fail because of it.
"""
import argparse
import json
import os
import sys

from alasio.ext import env

# Prefix of the lines on stderr, the channel a machine does not read
TAG = '[pyc_warm]'


def main(argv=None):
    """
    Warm the bytecode caches of an environment, the entry of the module.

    Args:
        argv (list[str]): Arguments of the command line. Defaults to
            None, sys.argv[1:]

    Returns:
        int: Exit code, 0 when the run started, 1 when the root does not
            exist
    """
    parser = argparse.ArgumentParser(
        prog='python -m alasio.deploy.pyc_warm',
        description='Compile the bytecode cache of the environment and the mods in advance.',
    )
    parser.add_argument(
        '--root', default=None, metavar='DIR',
        help='project root, the tree the mods declare themselves in, '
             'defaults to the working directory',
    )
    parser.add_argument(
        '--json', action='store_true',
        help='print the summary as one line of JSON on stdout',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='list the sources to compile, compile nothing',
    )
    args = parser.parse_args(argv)

    root = args.root if args.root is not None else os.getcwd()
    if not os.path.isdir(root):
        print(f'{TAG} root does not exist: {root}', file=sys.stderr)
        return 1
    # The mods of the run are read through the loader of the backend, which
    # is bound to the project root when it is imported: the root is set
    # before anything of the scope is imported
    env.set_project_root(root)

    # The imports are local: the scope reads the loader of the backend, and
    # the loader must see the project root set above
    from alasio.deploy.pyc_warm.runner import warm
    from alasio.deploy.pyc_warm.scope import Scope
    from alasio.logger import logger

    if sys.dont_write_bytecode:
        # py_compile writes the files whatever this flag says, it is the
        # modules the run imports itself that would be compiled again
        sys.dont_write_bytecode = False

    if args.json:
        # The parent reads one line of JSON on stdout: the lines of the
        # report have no reader there, and the log file of the release
        # must stay closed, the backend opens it at the same moment
        logger.mute(all=True)

    report = warm(Scope(), dry_run=args.dry_run)
    logger.info(f'total   : {report.seconds:.2f}s')
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
