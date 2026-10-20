"""
Build and verify the distribution artifacts from the command line.

    python -m alasio.deploy_dev.wheel build --root . --folder alasio

Build the wheel and the sdist of the commit being checked out into dist/.
The build reads the git objects of the commit, the frontend build in
frontend/build and the webapp build in webapp/dist, see BuildWheel.

    python -m alasio.deploy_dev.wheel build --require-static

Fail before the build when a build input is missing. A wheel without the
frontend or without the webapp is a valid wheel that no release job should
publish, so the release job builds with this flag.

    python -m alasio.deploy_dev.wheel verify dist

Verify the artifacts of a directory, or the artifacts that are listed. The
exit code is 0 when every artifact passed, 1 when one failed, 2 when the
command line is invalid.
"""
import argparse

from alasio.deploy_dev.wheel.build_wheel import BuildWheel
from alasio.deploy_dev.wheel.verify import WheelVerifyError, check_build_inputs, collect_artifacts, verify_artifact
from alasio.logger import logger


def _cmd_build(args):
    """
    Build the wheel and the sdist.

    Args:
        args (argparse.Namespace): Parsed command line

    Returns:
        int: Exit code
    """
    if args.require_static:
        problems = check_build_inputs(args.root)
        for problem in problems:
            logger.error(problem)
        if problems:
            logger.error('Build inputs are incomplete, the wheel would carry no frontend / no webapp')
            return 1

    build = BuildWheel(args.root, args.folder)
    try:
        success = build.build()
    except Exception as e:
        logger.error(f'Build failed: {e}')
        logger.exception(e)
        return 1
    return 0 if success else 1


def _cmd_verify(args):
    """
    Verify the artifacts of the command line.

    Args:
        args (argparse.Namespace): Parsed command line

    Returns:
        int: Exit code
    """
    files, problems = collect_artifacts(args.paths)
    for problem in problems:
        logger.error(problem)
    if not files:
        logger.error('No wheel or sdist to verify')
        return 1

    failed = False
    for file in files:
        try:
            report = verify_artifact(file, folder=args.folder)
        except WheelVerifyError as e:
            failed = True
            logger.error(f'Failed to verify {file}')
            for problem in e.problems:
                logger.error(f'  {problem}')
        except Exception as e:
            failed = True
            logger.error(f'Failed to verify {file}: {e}')
            logger.exception(e)
        else:
            logger.info(f'Verified {report}')
    return 1 if failed or problems else 0


def main(argv=None):
    """
    Command line entry.

    Args:
        argv (list[str]): Command line arguments, None to read sys.argv

    Returns:
        int: Exit code

    Raises:
        SystemExit: If the command line is invalid
    """
    parser = argparse.ArgumentParser(
        prog='python -m alasio.deploy_dev.wheel',
        description='Build and verify the wheel and the sdist of the project',
    )
    sub = parser.add_subparsers(dest='command', metavar='command')

    build = sub.add_parser('build', help='build the wheel and the sdist')
    build.add_argument(
        '-r', '--root', default='.',
        help='project root, default to the current directory',
    )
    build.add_argument(
        '-f', '--folder', default='alasio',
        help='top level folder of the library in the repo, default to "alasio"',
    )
    build.add_argument(
        '--require-static', action='store_true',
        help='fail when the frontend or the webapp build output is missing',
    )

    verify = sub.add_parser('verify', help='verify built wheels and sdists')
    verify.add_argument(
        'paths', nargs='*', default=['dist'],
        help='artifact files, or directories that hold them, default to "dist"',
    )
    verify.add_argument(
        '-f', '--folder', default='alasio',
        help='top level folder of the library in the wheel, default to "alasio"',
    )

    args = parser.parse_args(argv)
    if args.command is None:
        parser.error('missing command, choose from "build", "verify"')
    if args.command == 'build':
        return _cmd_build(args)
    return _cmd_verify(args)


if __name__ == '__main__':
    raise SystemExit(main())
