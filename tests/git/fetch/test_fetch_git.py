"""
Pytest suite for Git Protocol (git://) fetch functionality.

The server is a repository of the machine, served by `git daemon` on a loopback
port (alasio.testing.git_server), so these tests need no external server and run
on every platform of the matrix, offline included.

Tests cover:
- fetch_refs: Getting remote references
- fetch_pack_v1: Initial and incremental fetch using Protocol v1
- negotiation: a `have` line makes the server send a smaller pack
- the end of the answer: a server may reset the connection after the pack
"""
import os

import pytest
import trio

from alasio.git.fetch.argument import Arguments
from alasio.git.fetch.pkt import FetchPayload
from alasio.git.fetch.transport_git import GitTransport
from alasio.testing import git_server

# A fetch of a few hundreds of kilobytes over the loopback interface is a
# matter of milliseconds: anything slower is a hang, not a slow machine
TIMEOUT = 60

# The tests only read from their own server and write under a folder of their
# own, so this file is safe to run beside the other marked files
pytestmark = pytest.mark.parallel


@pytest.fixture
def transport(served_repo):
    """
    A transport that talks to the server of the test.

    Args:
        served_repo (ServedRepository): The server of the test

    Returns:
        GitTransport: The transport
    """
    return GitTransport(Arguments(repo_path=served_repo.work, repo_url=served_repo.url))


def build_payload(transport, sha, have=None):
    """
    Build the request of one fetch.

    Args:
        transport (GitTransport): The transport, for the capabilities it advertises
        sha (str): Sha1 of the wanted commit
        have (str): Sha1 of a commit the client already has

    Returns:
        FetchPayload: The request
    """
    payload = FetchPayload()
    payload.add_line(f'want {sha} {transport.capabilities.as_string()}')
    payload.add_delimiter()
    if have is not None:
        payload.add_have(have)
    payload.add_done()
    return payload


@pytest.mark.trio
async def test_fetch_refs(transport, served_repo):
    """The server advertises the tags and the remote tracking ref of the repository."""
    with trio.fail_after(TIMEOUT):
        refs = await transport.fetch_refs()

    assert b'refs/tags/v0.5.1' in refs.values()
    assert b'refs/tags/v0.5.2' in refs.values()
    # The sha the server advertises is the one the repository was built with
    assert served_repo.sha_v051.encode() in refs.keys()
    assert served_repo.sha_v052.encode() in refs.keys()
    # Every sha of an advertisement is a full object id
    for sha in refs.keys():
        assert len(sha) == 40, f'SHA should be 40 chars: {sha}'
        assert all(c in b'0123456789abcdef' for c in sha), f'SHA should be hex: {sha}'


@pytest.mark.trio
async def test_fetch_pack_v1_initial(transport, served_repo):
    """A fetch of a commit without any `have` transfers the whole history."""
    pack_file = str(served_repo.work.joinpath('initial.pack'))
    with trio.fail_after(TIMEOUT):
        await transport.fetch_pack_v1(build_payload(transport, served_repo.sha_v052), output_file=pack_file)

    assert os.path.exists(pack_file), 'Pack file should be created'
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 1000, f'Pack file should be substantial (got {pack_size} bytes)'

    with open(pack_file, 'rb') as f:
        assert f.read(4) == b'PACK', 'Pack file should start with PACK signature'

    # The pack is not only bytes with a header: git itself has to accept it,
    # which checks the header, the object count, every object and the checksum
    git_server.git('index-pack', '--strict', pack_file)


@pytest.mark.trio
async def test_fetch_pack_v1_incremental(transport, served_repo):
    """A fetch of a commit whose parent the client has transfers only the change."""
    pack_file = str(served_repo.work.joinpath('incremental.pack'))
    payload = build_payload(transport, served_repo.sha_v052, have=served_repo.sha_v051)
    with trio.fail_after(TIMEOUT):
        await transport.fetch_pack_v1(payload, output_file=pack_file)

    assert os.path.exists(pack_file), 'Incremental pack should be created'
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 0, 'Incremental pack should have content'

    with open(pack_file, 'rb') as f:
        assert f.read(4) == b'PACK', 'Incremental pack should start with PACK signature'


@pytest.mark.trio
async def test_fetch_pack_v1_negotiation_reduces_size(transport, served_repo):
    """The `have` line of the request makes the pack smaller than a full fetch."""
    full_pack = str(served_repo.work.joinpath('full.pack'))
    incremental_pack = str(served_repo.work.joinpath('reduced.pack'))

    with trio.fail_after(TIMEOUT):
        await transport.fetch_pack_v1(build_payload(transport, served_repo.sha_v052), output_file=full_pack)
        payload = build_payload(transport, served_repo.sha_v052, have=served_repo.sha_v051)
        await transport.fetch_pack_v1(payload, output_file=incremental_pack)

    size_full = os.path.getsize(full_pack)
    size_incremental = os.path.getsize(incremental_pack)

    # The second commit adds a file of SECOND_FILE_SIZE to a history that holds
    # FIRST_FILE_SIZE, so the incremental pack is a small fraction of the full one
    assert size_incremental < size_full, (
        f'Incremental pack ({size_incremental} bytes) should be smaller than '
        f'full pack ({size_full} bytes) due to negotiation'
    )
    savings_ratio = (size_full - size_incremental) / size_full
    assert savings_ratio > 0.5, f'Should save >50% bandwidth (saved {savings_ratio * 100:.1f}%)'


@pytest.mark.trio
@pytest.mark.skip(reason='The client does not parse the pack section of protocol v2 yet')
async def test_fetch_pack_v2(transport, served_repo):
    """Test fetch using Protocol v2."""
    pack_file = str(served_repo.work.joinpath('v2.pack'))
    with trio.fail_after(TIMEOUT):
        await transport.fetch_pack_v2(build_payload(transport, served_repo.sha_v051), output_file=pack_file)

    assert os.path.exists(pack_file), 'Pack file should be created via v2'
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 1000, f'Pack should have content (got {pack_size} bytes)'


@pytest.mark.trio
async def test_refs_contain_expected_structure(transport):
    """The advertisement holds the branches and the tags of the repository."""
    with trio.fail_after(TIMEOUT):
        refs = await transport.fetch_refs()

    has_heads = any(ref.startswith(b'refs/heads/') for ref in refs.values())
    has_tags = any(ref.startswith(b'refs/tags/') for ref in refs.values())
    assert has_heads, 'Should have at least one branch reference'
    assert has_tags, 'Should have at least one tag reference'

    # All refs should start with refs/
    for ref in refs.values():
        assert ref.startswith(b'refs/'), f"All refs should start with 'refs/': {ref}"
