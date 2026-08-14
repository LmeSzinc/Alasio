"""
Tests for history encoding: conversion from CommitObj / TagObject to
HistoryObj, and msgpack encoding of history lists.
"""
import pytest

from alasio.deploy.history.decode_history import HistoryObj, decode_history
from alasio.deploy_dev.history.encode_history import (
    commit_to_history, encode_commit_history, encode_tag_history, split_commit_message, tag_to_history
)
from alasio.git.obj.objcommit import CommitObj
from alasio.git.obj.objtag import TagObject

SHA1_A = 'a' * 40
SHA1_B = 'b' * 40


def _make_commit(message='', author_name='Author', author_time=900, committer_time=1000):
    """
    Build a CommitObj with fixed values for the other fields.

    Args:
        message (str): Commit message
        author_name (str): Author name
        author_time (int): Author time
        committer_time (int): Committer time

    Returns:
        CommitObj:
    """
    return CommitObj(
        tree='tree' * 5,
        parent=None,
        author_name=author_name,
        author_email='author@example.com',
        author_time=author_time,
        author_tz=0,
        committer_name='Committer',
        committer_email='committer@example.com',
        committer_time=committer_time,
        committer_tz=0,
        message=message,
    )


def _make_tag(tag='v1.0.0', tagger_name='Tagger', tagger_time=2000):
    """
    Build a TagObject with fixed values for the other fields.

    Args:
        tag (str): Tag name
        tagger_name (str): Tagger name
        tagger_time (int): Tagger time

    Returns:
        TagObject:
    """
    return TagObject(
        object='object' * 5,
        type='commit',
        tag=tag,
        tagger_name=tagger_name,
        tagger_email='tagger@example.com',
        tagger_time=tagger_time,
        tagger_tz=0,
        message='',
    )


class TestSplitCommitMessage:
    """Split a commit message into title and detail."""

    @pytest.mark.parametrize("message, expected", [
        # title only
        ('Title', ('Title', '')),
        # title and detail
        ('Title\nBody', ('Title', 'Body')),
        # blank line between title and detail
        ('Title\n\nBody', ('Title', 'Body')),
        # multi-line detail
        ('Title\nline1\nline2', ('Title', 'line1\nline2')),
        # empty message
        ('', ('', '')),
        # whitespace only
        ('  \n  ', ('', '')),
        # trailing whitespace on the title line
        ('Title  \nBody', ('Title', 'Body')),
        # trailing newline in message
        ('Title\nBody\n', ('Title', 'Body')),
        # chinese message
        ('标题\n正文第一行\n正文第二行', ('标题', '正文第一行\n正文第二行')),
    ])
    def test_split(self, message, expected):
        """Message is split at the first newline."""
        assert split_commit_message(message) == expected


class TestCommitToHistory:
    """Convert CommitObj to HistoryObj."""

    def test_version_is_commit_sha1(self):
        """The version of a commit is its sha1."""
        history = commit_to_history(SHA1_A, _make_commit())
        assert history.version == SHA1_A

    def test_author_is_author_name(self):
        """The author of a commit is the author name."""
        history = commit_to_history(SHA1_A, _make_commit(author_name='Author'))
        assert history.author == 'Author'

    def test_time_is_author_time(self):
        """The time of a commit is the author time, not the committer time."""
        history = commit_to_history(SHA1_A, _make_commit(author_time=900, committer_time=1000))
        assert history.time == 900

    def test_title_and_detail(self):
        """The title and detail come from the commit message."""
        history = commit_to_history(SHA1_A, _make_commit(message='Title\n\nBody'))
        assert history.title == 'Title'
        assert history.detail == 'Body'

    def test_empty_message(self):
        """An empty commit message gives an empty title and detail."""
        history = commit_to_history(SHA1_A, _make_commit(message=''))
        assert history.title == ''
        assert history.detail == ''


class TestTagToHistory:
    """Convert TagObject to HistoryObj."""

    def test_version_is_tag_name(self):
        """The version of a tag is the tag name."""
        history = tag_to_history(_make_tag(tag='v1.0.0'))
        assert history.version == 'v1.0.0'

    def test_author_is_tagger_name(self):
        """The author of a tag is the tagger name."""
        history = tag_to_history(_make_tag(tagger_name='Tagger'))
        assert history.author == 'Tagger'

    def test_time_is_tagger_time(self):
        """The time of a tag is the tagger time."""
        history = tag_to_history(_make_tag(tagger_time=2000))
        assert history.time == 2000

    def test_title_and_detail_empty(self):
        """A tag has no title and no detail."""
        history = tag_to_history(_make_tag())
        assert history.title == ''
        assert history.detail == ''


class TestEncodeCommitHistory:
    """Encode commit history from a dict of sha1 to CommitObj."""

    def test_encode_decode_roundtrip(self):
        """Encoded bytes must decode back to the converted history."""
        commits = {
            SHA1_A: _make_commit(message='Title\nBody', author_name='Author', author_time=1000),
            SHA1_B: _make_commit(message='', author_time=2000),
        }
        data = encode_commit_history(commits)
        assert decode_history(data) == [
            HistoryObj(version=SHA1_A, author='Author', time=1000, title='Title', detail='Body'),
            HistoryObj(version=SHA1_B, author='Author', time=2000, title='', detail=''),
        ]

    def test_empty_history(self):
        """An empty commit dict encodes to an empty msgpack array."""
        assert encode_commit_history({}) == b'\x90'


class TestEncodeTagHistory:
    """Encode tag history from a list of TagObject."""

    def test_encode_decode_roundtrip(self):
        """Encoded bytes must decode back to the converted history."""
        tags = [
            _make_tag(tag='v1.0.0', tagger_name='Tagger', tagger_time=2000),
            _make_tag(tag='v2.0.0', tagger_name='Tagger', tagger_time=3000),
        ]
        data = encode_tag_history(tags)
        assert decode_history(data) == [
            HistoryObj(version='v1.0.0', author='Tagger', time=2000, title='', detail=''),
            HistoryObj(version='v2.0.0', author='Tagger', time=3000, title='', detail=''),
        ]

    def test_empty_history(self):
        """An empty tag list encodes to an empty msgpack array."""
        assert encode_tag_history([]) == b'\x90'
