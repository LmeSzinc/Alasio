import zlib
from zlib import decompress

import msgspec

from alasio.gitpython.file.exception import ObjectBroken
from alasio.gitpython.obj.objcommit import tz2delta


class TagObject(msgspec.Struct):
    # the object sha1 that this tag the pointing to
    object: str
    # usually to be "commit"
    type: str
    # tag name, usually to be a version
    tag: str
    # tagger
    tagger_name: str
    tagger_email: str
    tagger_time: int
    # note that time is in UTC+0

    # usually to be a GPG key
    message: str


def parse_tag_object(data):
    """
    Get the object sha1 that this tag the pointing to

    Args:
        data (memoryview):

    Returns:
        str:
    """
    try:
        remain = decompress(data)
    except zlib.error as e:
        raise ObjectBroken(str(e), data)

    # object
    row, _, remain = remain.partition(b'\n')
    key, _, obj = row.partition(b' ')
    if key != b'object':
        raise ObjectBroken(f'Object should startswith "object" not "{key}"', data)
    try:
        return obj.decode()
    except ValueError:
        raise ObjectBroken(f'Tag object of tag is not a sha1: {obj}', data)


def parse_tag(data):
    """
    Get full info from a git commit object

    Args:
        data (memoryview):

    Returns:
        TagObject:
    """
    try:
        remain = decompress(data)
    except zlib.error as e:
        raise ObjectBroken(str(e), data)

    # object
    row, _, remain = remain.partition(b'\n')
    key, _, obj = row.partition(b' ')
    if key != b'object':
        raise ObjectBroken(f'Object should startswith "object" not "{key}"', data)
    try:
        obj = obj.decode()
    except ValueError:
        raise ObjectBroken(f'Tag object of tag is not a sha1: {obj}', data)

    # type
    row, _, remain = remain.partition(b'\n')
    key, _, typ = row.partition(b' ')
    if key != b'type':
        raise ObjectBroken(f'Object should have "type" not "{key}"', data)
    try:
        typ = typ.decode()
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode tag type: "{typ}"', data)

    # tag
    row, _, remain = remain.partition(b'\n')
    key, _, tag = row.partition(b' ')
    if key != b'tag':
        raise ObjectBroken(f'Object should have "tag" not "{key}"', data)
    try:
        tag = tag.decode('utf-8')
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode tag name: "{tag}"', data)

    # tagger
    # same format as committer in commit
    row, _, remain = remain.partition(b'\n')
    if not row.startswith(b'tagger'):
        raise ObjectBroken(f'Tag object should have "tagger" not "{row}"', data)
    try:
        key, tagger_name, tagger_email, tagger_time, tz = row.split(b' ')
    except ValueError:
        raise ObjectBroken(f'Unexpected element amount in "tagger": {row}', data)
    try:
        tagger_name = tagger_name.decode('utf-8')
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode tagger name: "{tagger_name}"', data)
    try:
        tagger_email = tagger_email.strip(b'<>').decode('utf-8')
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode tagger name: "{tagger_email}"', data)
    try:
        tagger_time = int(tagger_time)
    except ValueError:
        raise ObjectBroken(f'tagger time is not int: "{tagger_time}"', data)
    try:
        tz = tz2delta(tz)
    except ValueError:
        raise ObjectBroken(f'Failed to parse tagger timezone: "{tz}"', data)
    tagger_time += tz

    # message
    _, _, message = remain.partition(b'\n\n')
    try:
        message = message.strip().decode('utf-8')
    except UnicodeDecodeError:
        raise ObjectBroken(f'Failed to decode commit message: "{message}"', data)

    return TagObject(
        object=obj,
        type=typ,
        tag=tag,
        tagger_name=tagger_name,
        tagger_email=tagger_email,
        tagger_time=tagger_name,
        message=message,
    )
