# This file is a collection of magic matches to provide consistent behaviour on all deployment

def patch_mimetype():
    """
    Patch mimetype db to use the builtin table instead of reading from environment.

    By default, mimetype reads user configured mimetype table from environment. It's good for server but bad on our
    side, because we deploy on user's machine which may have polluted environment. To have a consistent behaviour on
    all deployment, we use the builtin mimetype table only.
    """
    import mimetypes
    if mimetypes.inited:
        # ohno mimetypes already inited
        db = mimetypes.MimeTypes()
        mimetypes._db = db
        # override global variable
        mimetypes.encodings_map = db.encodings_map
        mimetypes.suffix_map = db.suffix_map
        mimetypes.types_map = db.types_map[True]
        mimetypes.common_types = db.types_map[False]
    else:
        # init db with the default table
        db = mimetypes.MimeTypes()
        mimetypes._db = db
        mimetypes.inited = True


def patch_std():
    """
    Force use utf-8 in stdin, stdout, stderr, ignoring any user env.

    Returns:
        bool: if success
    """
    # note that reconfigure() requires python>=3.7
    import sys
    try:
        sys.stdin.reconfigure(encoding='utf-8')
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
        return True
    except (AttributeError, TypeError):
        # std may get replaced by user's TextIO
        # which does not have reconfigure()
        return False


def patch_environ():
    """
    Remove all python related environs, updated to python 3.15
    Note that some environs effects python interpreter startup, removing them at runtime won't work.

    You can AI to extract the list from https://docs.python.org/3/using/cmdline.html
    """
    python_environment_variables = [
        "FORCE_COLOR",
        "NO_COLOR",
        "PYTHONASYNCIODEBUG",
        "PYTHONBREAKPOINT",
        "PYTHONCASEOK",
        "PYTHONCOERCECLOCALE",
        "PYTHONDEBUG",
        "PYTHONDEVMODE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONDUMPREFS",
        "PYTHONDUMPREFSFILE",
        "PYTHONEXECUTABLE",
        "PYTHONFAULTHANDLER",
        "PYTHONHASHSEED",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONINTMAXSTRDIGITS",
        "PYTHONIOENCODING",
        "PYTHONLEGACYWINDOWSFSENCODING",
        "PYTHONLEGACYWINDOWSSTDIO",
        "PYTHONMALLOC",
        "PYTHONMALLOCSTATS",
        "PYTHONNODEBUGRANGES",
        "PYTHONNOUSERSITE",
        "PYTHONOPTIMIZE",
        "PYTHONPATH",
        "PYTHONPERFSUPPORT",
        "PYTHONPLATLIBDIR",
        "PYTHONPROFILEIMPORTTIME",
        "PYTHONPYCACHEPREFIX",
        "PYTHONSAFEPATH",
        "PYTHONSTARTUP",
        "PYTHONTRACEMALLOC",
        "PYTHONUNBUFFERED",
        "PYTHONUSERBASE",
        "PYTHONUTF8",
        "PYTHONVERBOSE",
        "PYTHONWARNDEFAULTENCODING",
        "PYTHONWARNINGS",
        "PYTHON_BASIC_REPL",
        "PYTHON_COLORS",
        "PYTHON_CONTEXT_AWARE_WARNINGS",
        "PYTHON_CPU_COUNT",
        "PYTHON_DISABLE_REMOTE_DEBUG",
        "PYTHON_FROZEN_MODULES",
        "PYTHON_GIL",
        "PYTHON_HISTORY",
        "PYTHON_JIT",
        "PYTHON_PERF_JIT_SUPPORT",
        "PYTHON_PRESITE",
        "PYTHON_THREAD_INHERIT_CONTEXT",
        "PYTHON_TLBC",
    ]
    proxy_environment_variables = [
        'HTTP_PROXY',
        'HTTPS_PROXY',
        'ALL_PROXY',
        'NO_PROXY',
        'HTTPPROXY',
        'HTTPSPROXY',
        'ALLPROXY',
        'NOPROXY',
    ]
    removes = set(python_environment_variables + proxy_environment_variables)

    import os
    # listify to safely iterate
    environs = [key for key in os.environ]
    for key in environs:
        upper = key.upper()
        if upper in removes:
            os.environ.pop(key, None)


def patch_startup():
    """
    A collection of patches on process startup
    This function is supposed to be called in entry file (outside of `if __name__ == "__main__":`)
    """
    patch_environ()
    patch_std()
