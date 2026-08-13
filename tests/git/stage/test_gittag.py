import hashlib
import zlib

import pytest

from alasio.git.stage.gittag import GitTag
from alasio.testing.filesystem import fs  # noqa: F401


def make_loose_object(fs, repo, objtype, content):
    """
    Create a loose object in the in-memory filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        repo (str): Repository path
        objtype (str): Object type, e.g. "commit", "tag", "blob"
        content (bytes): Object content

    Returns:
        str: sha1 of the object
    """
    header = f'{objtype} {len(content)}\x00'.encode()
    data = header + content
    sha1 = hashlib.sha1(data).hexdigest()
    fs.create_file(f'{repo}/.git/objects/{sha1[:2]}/{sha1[2:]}', contents=zlib.compress(data))
    return sha1


@pytest.fixture
def git_tag(fs):
    """
    Create a repository with one commit, one annotated tag and one blob tag.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture

    Returns:
        GitTag: GitTag instance, object database read
    """
    root = fs.root_dir.path.rstrip('/\\')
    repo = f'{root}/repo'
    fs.create_dir(f'{repo}/.git/refs/tags')
    fs.create_dir(f'{repo}/.git/objects')

    commit_content = (
        b'tree 2b07ca1800c558861022911371ce84a6e7116941\n'
        b'author LmeSzinc <lmeszincsales@gmail.com> 1585635715 +0800\n'
        b'committer LmeSzinc <lmeszincsales@gmail.com> 1585635715 +0800\n'
        b'\n'
        b'commit message'
    )
    commit_sha1 = make_loose_object(fs, repo, 'commit', commit_content)

    tag_content = (
        f'object {commit_sha1}\n'.encode()
        + b'type commit\n'
        + b'tag v1.0\n'
        + b'tagger LmeSzinc <lmeszincsales@gmail.com> 1585635715 +0800\n'
        + b'\n'
        + b'tag message'
    )
    tag_sha1 = make_loose_object(fs, repo, 'tag', tag_content)

    blob_sha1 = make_loose_object(fs, repo, 'blob', b'hello')

    # v1.0: annotated tag, v2.0: lightweight tag, v3.0: tag to a blob
    fs.create_file(f'{repo}/.git/refs/tags/v1.0', contents=tag_sha1)
    fs.create_file(f'{repo}/.git/refs/tags/v2.0', contents=commit_sha1)
    fs.create_file(f'{repo}/.git/refs/tags/v3.0', contents=blob_sha1)

    tag = GitTag(repo)
    tag.read_lazy()
    return tag


class TestTags:
    def test_tags_list(self, git_tag):
        """All tag names are listed."""
        assert sorted(git_tag.tags) == ['v1.0', 'v2.0', 'v3.0']

    def test_tags_cached(self, git_tag):
        """tags is a cached property, same list instance on repeat access."""
        assert git_tag.tags is git_tag.tags

    def test_tags_empty(self, fs):
        """A repository without tags returns an empty list."""
        root = fs.root_dir.path.rstrip('/\\')
        repo = f'{root}/repo'
        fs.create_dir(f'{repo}/.git/refs/tags')

        tag = GitTag(repo)
        tag.read_lazy()
        assert tag.tags == []


class TestTagGet:
    def test_get_annotated(self, git_tag):
        """An annotated tag returns the parsed TagObject."""
        tag = git_tag.tag_get('v1.0')

        assert tag.tag == 'v1.0'
        assert tag.type == 'commit'
        assert tag.tagger_name == 'LmeSzinc'
        assert tag.tagger_email == 'lmeszincsales@gmail.com'
        # +0800, time is converted to UTC+0
        assert tag.tagger_tz == 480
        assert tag.tagger_time == 1585635715 + 480 * 60
        assert tag.message == 'tag message'

    def test_get_lightweight(self, git_tag):
        """A lightweight tag builds the tagger info from the commit."""
        tag = git_tag.tag_get('v2.0')

        # object is the commit sha1
        assert tag.tag == 'v2.0'
        assert tag.type == 'commit'
        # tagger info comes from the committer attributes of the commit
        assert tag.tagger_name == 'LmeSzinc'
        assert tag.tagger_email == 'lmeszincsales@gmail.com'
        assert tag.tagger_tz == 480
        assert tag.tagger_time == 1585635715 + 480 * 60
        assert tag.message == ''

    def test_get_missing(self, git_tag):
        """A tag that does not exist returns None."""
        assert git_tag.tag_get('v9.9') is None

    def test_get_blob_tag(self, git_tag):
        """A tag pointing to a blob returns None."""
        assert git_tag.tag_get('v3.0') is None
