"""
Pytest suite for Git Protocol (git://) fetch functionality.

Tests cover:
- fetch_refs: Getting remote references
- fetch_pack_v1: Initial and incremental fetch using Protocol v1
- fetch_pack_v2: Fetch using Protocol v2 (if server supports)
"""
import pytest
import trio
import os
import tempfile
from pathlib import Path

from alasio.git.fetch.argument import Arguments
from alasio.git.fetch.transport_git import GitTransport
from alasio.git.fetch.pkt import FetchPayload


# Test configuration
TEST_REPO_URL = "git://git.lyoko.io/AzurLaneAutoScript"
TEST_TAG_V051 = "50f49a6350aa584d96dc4efe162cec8ce09a212b"
TEST_TAG_V052 = "8b955975df6f7af8b8411f9b753ff84c26adf110"


@pytest.fixture
def temp_repo_path():
    """Create a temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def git_transport(temp_repo_path):
    """Create a GitTransport instance for testing."""
    args = Arguments(repo_path=temp_repo_path, repo_url=TEST_REPO_URL)
    return GitTransport(args)


@pytest.mark.network
@pytest.mark.trio
async def test_fetch_refs(git_transport):
    """Test fetching remote references via git:// protocol."""
    refs = await git_transport.fetch_refs()
    
    # Verify we got some refs
    assert len(refs) > 0, "Should receive at least one reference"
    
    # Check for expected tags
    assert b'refs/tags/v0.5.1' in refs.values(), "Should contain v0.5.1 tag"
    assert b'refs/tags/v0.5.2' in refs.values(), "Should contain v0.5.2 tag"
    
    # Verify SHA format (all should be 40 hex chars)
    for sha in refs.keys():
        assert len(sha) == 40, f"SHA should be 40 chars: {sha}"
        assert all(c in b'0123456789abcdef' for c in sha), f"SHA should be hex: {sha}"


@pytest.mark.network
@pytest.mark.trio
async def test_fetch_pack_v1_initial(git_transport, temp_repo_path):
    """Test initial fetch using Protocol v1 (no existing objects)."""
    # Build payload for v0.5.1
    payload = FetchPayload()
    payload.add_line(f"want {TEST_TAG_V051} {git_transport.capabilities.as_string()}")
    payload.add_delimiter()
    payload.add_done()
    
    # Fetch pack file
    pack_file = os.path.join(temp_repo_path, "initial.pack")
    await git_transport.fetch_pack_v1(payload, output_file=pack_file)
    
    # Verify pack file was created and has content
    assert os.path.exists(pack_file), "Pack file should be created"
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 1000, f"Pack file should be substantial (got {pack_size} bytes)"
    
    # Verify it starts with PACK signature
    with open(pack_file, 'rb') as f:
        signature = f.read(4)
        assert signature == b'PACK', "Pack file should start with PACK signature"


@pytest.mark.network
@pytest.mark.trio
async def test_fetch_pack_v1_incremental(git_transport, temp_repo_path):
    """Test incremental fetch using Protocol v1 (with negotiation)."""
    # Build payload requesting v0.5.2 but declaring we have v0.5.1
    payload = FetchPayload()
    payload.add_line(f"want {TEST_TAG_V052} {git_transport.capabilities.as_string()}")
    payload.add_delimiter()
    payload.add_have(TEST_TAG_V051)  # Tell server we already have this
    payload.add_done()
    
    # Fetch incremental pack
    pack_file = os.path.join(temp_repo_path, "incremental.pack")
    await git_transport.fetch_pack_v1(payload, output_file=pack_file)
    
    # Verify pack file was created
    assert os.path.exists(pack_file), "Incremental pack should be created"
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 0, "Incremental pack should have content"
    
    # Incremental pack should be smaller than full fetch (rough check)
    # In practice, v0.5.1 -> v0.5.2 should be much smaller than full v0.5.2
    assert pack_size < 15 * 1024 * 1024, "Incremental pack should be reasonably small"


@pytest.mark.network
@pytest.mark.trio
async def test_fetch_pack_v1_negotiation_reduces_size(git_transport, temp_repo_path):
    """Verify that negotiation (have) reduces pack size compared to initial fetch."""
    # First: Fetch v0.5.2 without any 'have' (initial)
    payload_full = FetchPayload()
    payload_full.add_line(f"want {TEST_TAG_V052} {git_transport.capabilities.as_string()}")
    payload_full.add_delimiter()
    payload_full.add_done()
    
    pack_full = os.path.join(temp_repo_path, "full.pack")
    await git_transport.fetch_pack_v1(payload_full, output_file=pack_full)
    size_full = os.path.getsize(pack_full)
    
    # Second: Fetch v0.5.2 with 'have v0.5.1' (incremental)
    payload_inc = FetchPayload()
    payload_inc.add_line(f"want {TEST_TAG_V052} {git_transport.capabilities.as_string()}")
    payload_inc.add_delimiter()
    payload_inc.add_have(TEST_TAG_V051)
    payload_inc.add_done()
    
    pack_inc = os.path.join(temp_repo_path, "inc.pack")
    await git_transport.fetch_pack_v1(payload_inc, output_file=pack_inc)
    size_inc = os.path.getsize(pack_inc)
    
    # Verify incremental is smaller
    assert size_inc < size_full, (
        f"Incremental pack ({size_inc} bytes) should be smaller than "
        f"full pack ({size_full} bytes) due to negotiation"
    )
    
    # Should save at least 50% for v0.5.1 -> v0.5.2
    savings_ratio = (size_full - size_inc) / size_full
    assert savings_ratio > 0.5, f"Should save >50% bandwidth (saved {savings_ratio*100:.1f}%)"


@pytest.mark.network
@pytest.mark.trio
@pytest.mark.skip(reason="Not all git:// servers support Protocol v2")
async def test_fetch_pack_v2(git_transport, temp_repo_path):
    """Test fetch using Protocol v2."""
    # Build v1 payload (will be converted to v2 internally)
    payload = FetchPayload()
    payload.add_line(f"want {TEST_TAG_V051} {git_transport.capabilities.as_string()}")
    payload.add_delimiter()
    payload.add_done()
    
    # Fetch using v2
    pack_file = os.path.join(temp_repo_path, "v2.pack")
    await git_transport.fetch_pack_v2(payload, output_file=pack_file)
    
    # Verify pack file
    assert os.path.exists(pack_file), "Pack file should be created via v2"
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 1000, f"Pack should have content (got {pack_size} bytes)"


@pytest.mark.network
@pytest.mark.trio
async def test_refs_contain_expected_structure(git_transport):
    """Verify the structure and content of fetched refs."""
    refs = await git_transport.fetch_refs()
    
    # Should have refs/tags, refs/heads, refs/remotes
    has_tags = any(ref.startswith(b'refs/tags/') for ref in refs.values())
    has_remotes = any(ref.startswith(b'refs/remotes/') for ref in refs.values())
    
    assert has_tags, "Should have at least one tag reference"
    assert has_remotes, "Should have at least one remote reference"
    
    # All refs should start with refs/
    for ref in refs.values():
        assert ref.startswith(b'refs/'), f"All refs should start with 'refs/': {ref}"
