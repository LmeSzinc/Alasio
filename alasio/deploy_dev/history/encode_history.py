from msgspec.msgpack import encode

from alasio.deploy.history.decode_history import HistoryObj


def split_commit_message(message):
    """
    Split a commit message into title and detail.

    The first line is the title, the rest is the detail. The detail is
    stripped of leading and trailing whitespace, so the blank line
    between the title and the body is removed.

    Args:
        message (str): Commit message

    Returns:
        (str, str): Title and detail
    """
    title, sep, rest = message.partition('\n')
    return title.strip(), rest.strip() if sep else ''


def commit_to_history(sha1, commit):
    """
    Convert a commit to a HistoryObj.

    Args:
        sha1 (str): Commit sha1, used as the version
        commit (CommitObj): Commit object

    Returns:
        HistoryObj:
    """
    title, detail = split_commit_message(commit.message)
    return HistoryObj(
        version=sha1,
        author=commit.author_name,
        time=commit.author_time,
        title=title,
        detail=detail,
    )


def tag_to_history(tag):
    """
    Convert a tag to a HistoryObj.

    Args:
        tag (TagObject): Tag object

    Returns:
        HistoryObj:
    """
    return HistoryObj(
        version=tag.tag,
        author=tag.tagger_name,
        time=tag.tagger_time,
        title='',
        detail='',
    )


def encode_commit_history(commits):
    """
    Encode commit history to msgpack bytes.

    CommitObj does not carry its sha1, so commits is a dict of
    sha1 to CommitObj, e.g. the output of GitCommit.list_commit_have().

    Args:
        commits (dict[str, CommitObj]): Commit sha1 to commit object

    Returns:
        bytes: msgpack encoded history data
    """
    return encode([
        commit_to_history(sha1, commit) for sha1, commit in commits.items()
    ])


def encode_tag_history(tags):
    """
    Encode tag history to msgpack bytes.

    Args:
        tags (list[TagObject]): Tag objects

    Returns:
        bytes: msgpack encoded history data
    """
    return encode([tag_to_history(tag) for tag in tags])
