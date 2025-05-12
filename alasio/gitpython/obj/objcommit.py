import zlib
from typing import List, Union
from zlib import decompress

import msgspec

from alasio.gitpython.file.exception import ObjectBroken


class CommitObj(msgspec.Struct):
    # sha1 of tree object, tree that records files in this commit
    tree: str
    # sha1 for parent object
    # 2 parent for merge commit
    # 1 parent for normal commit
    # 0 parent for initial commit
    parent: Union[List[str], str, None]

    author_name: str
    author_email: str
    author_time: int
    # note that time is in UTC+0

    committer_name: str
    committer_email: str
    committer_time: int

    # Do we need this?
    # gpgsig: Union[bytes, None]
    message: str


def tz2delta(tz):
    """
    Convert timezone bytes like b"+0800" or b"-0430" to seconds as int.

    Args:
        tz (bytes):

    Returns:
        int:

    Raises:
        ValueError: If failed to convert
    """
    if tz.startswith(b'+'):
        hour = int(tz[1:3])
        minute = int(tz[3:5])
        return hour * 3600 + minute * 60
    if tz.startswith(b'-'):
        hour = int(tz[1:3])
        minute = int(tz[3:5])
        return hour * -3600 + minute * -60
    hour = int(tz[0:2])
    minute = int(tz[2:4])
    return hour * 3600 + minute * 60


def parse_commit_tree(data):
    """
    Get tree sha1 from a git commit object.
    A light version of parse_commit when you need tree only

    Args:
        data (memoryview):

    Returns:
        str: sha1 of tree
    """
    try:
        data = decompress(data)
    except zlib.error as e:
        raise ObjectBroken(str(e), data)

    # tree is in the first row
    # tree fb3a8bdd0ceddd019615af4d57a53f43d8cee2bf
    row, _, _ = data.partition(b'\n')
    key, _, tree = row.partition(b' ')
    if key != b'tree':
        raise ObjectBroken(f'Object should startswith "tree" not "{key}"', data)
    try:
        return tree.decode()
    except ValueError:
        raise ObjectBroken(f'Commit tree is not a sha1: {tree}', data)


def parse_commit(data):
    """
    Get full info from a git commit object

    Args:
        data (memoryview):

    Returns:
        CommitObj:
    """
    try:
        data = decompress(data)
    except zlib.error as e:
        raise ObjectBroken(str(e), data)

    # tree
    row, _, remain = data.partition(b'\n')
    key, _, tree = row.partition(b' ')
    if key != b'tree':
        raise ObjectBroken(f'Object should startswith "tree" not "{key}"', data)
    try:
        tree = tree.decode()
    except ValueError:
        raise ObjectBroken(f'Commit tree is not a sha1: {tree}', data)

    # parent
    parent = b''
    while 1:
        if remain.startswith(b'author'):
            break
        row, _, remain = remain.partition(b'\n')
        key, _, value = row.partition(b' ')
        if key == b'parent':
            try:
                value = value.decode()
            except ValueError:
                raise ObjectBroken(f'Commit parent is not a sha1: {value}', data)
            if parent:
                if type(parent) is list:
                    # 3 parents
                    parent.append(value)
                else:
                    parent = [parent, value]
            else:
                parent = value
        if not remain:
            raise ObjectBroken('Commit object has no "author"', data)

    # author
    # we just checked remain is starts with "author"
    # author LmeSzinc <37934724+LmeSzinc@users.noreply.github.com> 1604563164 +0800
    row, _, remain = remain.partition(b'\n')
    try:
        key, author_name, author_email, author_time, tz = row.split(b' ')
    except ValueError:
        raise ObjectBroken(f'Unexpected element amount in "author": {row}', data)
    try:
        author_name = author_name.decode('utf-8')
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode author name: "{author_name}"', data)
    try:
        author_email = author_email.strip(b'<>').decode('utf-8')
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode author name: "{author_email}"', data)
    try:
        author_time = int(author_time)
    except ValueError:
        raise ObjectBroken(f'Author time is not int: "{author_time}"', data)
    try:
        tz = tz2delta(tz)
    except ValueError:
        raise ObjectBroken(f'Failed to parse author timezone: "{tz}"', data)
    author_time += tz

    # committer
    # keep \n in the remains
    row, sep, remain = remain.partition(b'\n')
    remain = sep + remain
    if not row.startswith(b'committer'):
        raise ObjectBroken('Commit object has no "committer"', data)
    try:
        key, committer_name, committer_email, committer_time, tz = row.split(b' ')
    except ValueError:
        raise ObjectBroken(f'Unexpected element amount in "committer": {row}', data)

    try:
        committer_name = committer_name.decode('utf-8')
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode committer name: "{committer_name}"', data)
    try:
        committer_email = committer_email.strip(b'<>').decode('utf-8')
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode committer name: "{committer_email}"', data)
    try:
        committer_time = int(committer_time)
    except ValueError:
        raise ObjectBroken(f'Committer time is not int: "{committer_time}"', data)
    try:
        tz = tz2delta(tz)
    except ValueError:
        raise ObjectBroken(f'Failed to parse committer timezone: "{tz}"', data)
    committer_time += tz

    # message
    _, _, message = remain.partition(b'\n\n')
    try:
        message = message.strip().decode('utf-8')
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode commit message: "{message}"', data)

    return CommitObj(
        tree=tree,
        parent=parent,
        author_name=author_name,
        author_email=author_email,
        author_time=author_time,
        committer_name=committer_name,
        committer_email=committer_email,
        committer_time=committer_time,
        message=message,
    )
