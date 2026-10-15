"""
Tests for history encoding: conversion from CommitObj / TagObject to
HistoryObj, and msgpack encoding of history lists.
"""
import pytest

from alasio.deploy.history.decode_history import HistoryObj, decode_history
from alasio.deploy_dev.history.encode_history import (
    commit_to_history, encode_commit_history, encode_tag_history, split_commit_message, tag_to_history
)
from alasio.git.obj.objcommit import CommitObj, parse_commit
from alasio.git.obj.objtag import TagObject, parse_tag
from tests.git.obj.test_objcommit import obj_signed_gpg, obj_signed_mergetag

SHA1_A = 'a' * 40
SHA1_B = 'b' * 40

# A tag object with a gpg signed message, the identity of the tagger replaced by
# Test <test@example.com>, the armored block is that of a real signed tag. git
# writes the signature of a tag at the end of its message, with no line prefix,
# and the message of a tag is what must not reach the history of a version.
obj_signed_tag = (
    b'object ' + b'c' * 40 + b'\n'
    b'type commit\n'
    b'tag v1.0.0\n'
    b'tagger Test <test@example.com> 1000000000 +0000\n'
    b'\n'
    b'v1.0.0\n'
    b'-----BEGIN PGP SIGNATURE-----\n'
    b'\n'
    b'iQIcBAABCAAGBQJXRGaRAAoJEK4lNiJ/afGBkX8QAJTQZROw1gFSOpaDfICOp/1G\n'
    b'ulTJJAxdVqrKgS2rGHnkwk3pVSDqQ9W99uHeDp5mCWRx2ytH5CdIR69PjrVnb4gA\n'
    b'ws6e6CEhFVlFkW33vDgaqL95GRKzxvWsEsBWRPu3tZk3MEVtt+n//y/nGAH0G3SX\n'
    b'/H4KbjGRWELnEuIF9nEVaX83SDzfmFVsrVduR/qJyl0K8HkpAVEfgAxDJRyKqIQW\n'
    b'8jMV0fEK3slWPkII+ZYpXRkejJFzlBHM/c2nLbDyXbEVyzGLIyVLwYsB43Bxd2wl\n'
    b'3B3qe/B896CEptUICU+D5gnY9QWmK+sbg0aaPRaUOiLqKTeMi0oPDG4E+ivv98r3\n'
    b'vq4fa64MQb2X+7vAMrvzAT95rFywDhrr4x90ByQUqHPLjfVCy5KGgmW7LkjehxBi\n'
    b'ukv1SkuA9YE7v/ObJCLznYygbkwTpPf5GjOG5EtpMWUGHds8s0XAGCR6RuvGvoFq\n'
    b'aaX/C43+fxZgQnzglXEPxZ+XZnQ7Wah4ZLwwfzhOx79ZfLKT23GKoaD4pIpXpR7m\n'
    b'QkORh0RTJ20zInQ62T64nfWfMn9bypreYC3zbmIVzrAUPCWaURWOEAxs8Gfa1yTy\n'
    b'eAl8x/9AvfYNS1rOkEVnqipWAbK3+3CJr3N14kzltZY4pD3KRrchxpYxl5dshKZ9\n'
    b'TZygxErcisbbsXDzP4pO\n'
    b'=q18x\n'
    b'-----END PGP SIGNATURE-----\n'
)


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

    def test_signed_tag_message_is_not_packed(self):
        """A tag that carries a gpg signature in its message packs no signature.

        The message of a tag is where git puts the signature of a signed tag,
        it must not reach the history of the version.
        """
        tag = parse_tag(obj_signed_tag)
        assert '-----BEGIN PGP SIGNATURE-----' in tag.message

        history = tag_to_history(tag)

        assert history.version == 'v1.0.0'
        assert history.author == 'Test'
        assert history.time == 1000000000
        assert history.title == ''
        assert history.detail == ''
        assert encode_tag_history([tag]) == (
            b'\x91\x95\xa6v1.0.0\xa4Test\xce;\x9a\xca\x00\xa0\xa0'
        )


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


class TestEncodeSignedCommit:
    """The history of a gpg signed commit carries no signature.

    The record is pinned byte by byte: a signature that reached it would be
    shipped to every client that reads the history pack of a version.
    """

    def test_signed_commit(self):
        """The record is version, author name, author time, title and detail."""
        assert b'gpgsig -----BEGIN PGP SIGNATURE-----' in obj_signed_gpg

        commit = parse_commit(obj_signed_gpg)
        data = encode_commit_history({SHA1_A: commit})

        # no armor, no signature body, no key id, no email of the author
        assert data == (
            b'\x91\x95\xd9(aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\xa4Test'
            b'\xcej\xa8q\x17\xd9*more classifiers for PyPI metadata (#1151)\xa0'
        )
        assert b'test@example.com' not in data

    def test_signed_mergetag_commit(self):
        """The mergetag header embeds the whole signed tag object of a merge."""
        assert b'\nmergetag ' in obj_signed_mergetag

        commit = parse_commit(obj_signed_mergetag)
        data = encode_commit_history({SHA1_B: commit})

        assert data == (
            b"\x91\x95\xd9(bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\xa4Test"
            b"\xcec\x8c\x1eB\xb2Merge tag 'v1.8.2'\xa6v1.8.2"
        )
        assert b'test@example.com' not in data


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
