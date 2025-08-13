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
