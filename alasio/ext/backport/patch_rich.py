import sys

from alasio.ext.backport.once import patch_once


@patch_once
def patch_rich_traceback_extract():
    """
    Patch rich.traceback Traceback.extract() to remove python version check on exceptiongroup

    Returns:
        bool: If Traceback.extract() can handle exceptiongroup
    """
    if sys.version_info >= (3, 11):
        # python>=3.11 have builtin exceptiongroup, no need to patch
        return True
    try:
        from rich.traceback import Traceback
    except ImportError:
        # no rich, no need to patch
        return True

    from importlib.metadata import version, PackageNotFoundError
    try:
        ver = version('rich')
    except PackageNotFoundError:
        # this shouldn't happen
        return True

    if ver in ['14.1.0', '14.2.0']:
        from alasio.ext.backport.rich_14_1 import RichTracebackBackport
    elif ver in ['14.3.0', '14.3.1']:
        from alasio.ext.backport.rich_14_3 import RichTracebackBackport
    else:
        return False

    # Apply Monkey Patch
    Traceback.extract = RichTracebackBackport.extract
