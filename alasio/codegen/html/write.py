"""
Write a generated text file only when its content changed.

A page is generated again on every run of the job that publishes it, and a run
that changes nothing must not touch the file: the publish record and the static
host then see the same bytes, and a watcher of the folder is not woken up for
nothing. The file is read first and its content compared with the new one, only
a different content is written, atomically, so a reader never sees half a page.

A file that is not utf-8 text is not a page this module wrote (a leftover of
another tool, a file of another encoding): its content is read as missing and
the new page replaces it.

Usage:
    from alasio.codegen.html import write_if_changed

    if write_if_changed('wheel/index.html', page):
        print('the page changed')
"""

from alasio.ext.path.atomic import atomic_read_text, atomic_write

__all__ = [
    'is_current',
    'write_if_changed',
]


def is_current(file, content):
    """
    Whether a file already holds a content.

    The line endings of the file are normalized to LF before the compare, so a
    checkout that wrote CRLF is not read as a change: the generator always
    writes LF and a rewrite would only flip the endings.

    Args:
        file (str): Path of the file
        content (str): Text the file is expected to hold

    Returns:
        bool: True when the file holds the content, False when it does not or
            cannot be read as utf-8 text
    """
    try:
        old = atomic_read_text(file)
    except FileNotFoundError:
        return False
    except UnicodeDecodeError:
        return False
    return old.replace('\r\n', '\n') == content


def write_if_changed(file, content):
    """
    Write a text file atomically, skipping the write when the file is current.

    Args:
        file (str): Path of the file, the parent folder is created when it is
            missing
        content (str): Text of the file

    Returns:
        bool: True when the file was written, False when it already held the
            content

    Raises:
        OSError: If the file cannot be read or written
    """
    if is_current(file, content):
        return False
    atomic_write(file, content)
    return True
