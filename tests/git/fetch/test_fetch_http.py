"""
Pytest suite for HTTP/HTTPS Protocol fetch functionality.

Tests cover:
- fetch_refs: Getting remote references via HTTP
- fetch_pack_v1: Initial and incremental fetch using Protocol v1
- fetch_pack_v2: Fetch using Protocol v2 (Modern GitHub/GitLab)
"""
import pytest
import trio
import os
import tempfile

from alasio.git.fetch.argument import Arguments
from alasio.git.fetch.transport_http import HttpTransport
from alasio.git.fetch.pkt import FetchPayload


# Test configuration - using GitHub as it's reliable and supports v2
TEST_REPO_URL = "https://github.com/LmeSzinc/AzurLaneAutoScript"
TEST_TAG_V051 = "50f49a6350aa584d96dc4efe162cec8ce09a212b"
TEST_TAG_V052 = "8b955975df6f7af8b8411f9b753ff84c26adf110"


@pytest.fixture
def temp_repo_path():
    """Create a temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def http_transport(temp_repo_path):
    """Create an HttpTransport instance for testing."""
    args = Arguments(repo_path=temp_repo_path, repo_url=TEST_REPO_URL)
    return HttpTransport(args)


@pytest.mark.network
@pytest.mark.trio
async def test_fetch_refs_http(http_transport):
    """Test fetching remote references via HTTPS."""
    refs = await http_transport.fetch_refs()
    
    # Verify we got many refs (GitHub repos typically have lots)
    assert len(refs) > 10, f"Should receive multiple references (got {len(refs)})"
    
    # Check for expected tags
    assert b'refs/tags/v0.5.1' in refs.values(), "Should contain v0.5.1 tag"
    assert b'refs/tags/v0.5.2' in refs.values(), "Should contain v0.5.2 tag"
    assert b'refs/heads/master' in refs.values(), "Should contain master branch"
    
    # Verify SHA format
    for sha in refs.keys():
        assert len(sha) == 40, f"SHA should be 40 chars: {sha}"
        assert sha.isalnum(), f"SHA should be alphanumeric: {sha}"


@pytest.mark.network
@pytest.mark.trio
async def test_fetch_pack_v1_initial_http(http_transport, temp_repo_path):
    """Test initial fetch via HTTP using Protocol v1."""
    # Build payload for v0.5.1
    payload = FetchPayload()
    payload.add_line(f"want {TEST_TAG_V051} {http_transport.capabilities.as_string()}")
    payload.add_delimiter()
    payload.add_done()
    
    # Fetch pack file
    pack_file = os.path.join(temp_repo_path, "http_initial.pack")
    await http_transport.fetch_pack_v1(payload, output_file=pack_file)
    
    # Verify pack file
    assert os.path.exists(pack_file), "Pack file should be created"
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 1000, f"Pack file should be substantial (got {pack_size} bytes)"
    
    # Verify PACK signature
    with open(pack_file, 'rb') as f:
        signature = f.read(4)
        assert signature == b'PACK', "Pack file should start with PACK signature"


@pytest.mark.network
@pytest.mark.trio
async def test_fetch_pack_v1_incremental_http(http_transport, temp_repo_path):
    """Test incremental fetch via HTTP using negotiation."""
    # Request v0.5.2 while declaring we have v0.5.1
    payload = FetchPayload()
    payload.add_line(f"want {TEST_TAG_V052} {http_transport.capabilities.as_string()}")
    payload.add_delimiter()
    payload.add_have(TEST_TAG_V051)
    payload.add_done()
    
    # Fetch incremental pack
    pack_file = os.path.join(temp_repo_path, "http_incremental.pack")
    await http_transport.fetch_pack_v1(payload, output_file=pack_file)
    
    # Verify pack file
    assert os.path.exists(pack_file), "Incremental pack should be created"
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 0, "Incremental pack should have content"
    
    # Should be relatively small for v0.5.1 -> v0.5.2
    assert pack_size < 10 * 1024 * 1024, "Incremental pack should be reasonable size"


@pytest.mark.network
@pytest.mark.trio
async def test_fetch_pack_v2_http(http_transport, temp_repo_path):
    """Test fetch using HTTP Protocol v2 (modern Git servers like GitHub)."""
    # Build v1 payload (will be converted to v2)
    payload = FetchPayload()
    payload.add_line(f"want {TEST_TAG_V051} {http_transport.capabilities.as_string()}")
    payload.add_delimiter()
    payload.add_done()
    
    # Fetch using v2
    pack_file = os.path.join(temp_repo_path, "http_v2.pack")
    await http_transport.fetch_pack_v2(payload, output_file=pack_file)
    
    # Verify pack file
    assert os.path.exists(pack_file), "Pack file should be created via Protocol v2"
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 1000, f"Pack should have content (got {pack_size} bytes)"
    
    # Verify PACK signature
    with open(pack_file, 'rb') as f:
        signature = f.read(4)
        assert signature == b'PACK', "Pack file should have valid signature"


@pytest.mark.network
@pytest.mark.trio
async def test_fetch_pack_v2_incremental_http(http_transport, temp_repo_path):
    """Test incremental fetch using HTTP Protocol v2 with negotiation."""
    # Request v0.5.2 with have v0.5.1
    payload = FetchPayload()
    payload.add_line(f"want {TEST_TAG_V052} {http_transport.capabilities.as_string()}")
    payload.add_delimiter()
    payload.add_have(TEST_TAG_V051)
    payload.add_done()
    
    # Fetch using v2
    pack_file = os.path.join(temp_repo_path, "http_v2_inc.pack")
    await http_transport.fetch_pack_v2(payload, output_file=pack_file)
    
    # Verify pack file
    assert os.path.exists(pack_file), "Incremental v2 pack should be created"
    pack_size = os.path.getsize(pack_file)
    assert pack_size > 0, "Pack should have content"


@pytest.mark.network
@pytest.mark.trio
async def test_http_negotiation_bandwidth_savings(http_transport, temp_repo_path):
    """Verify that HTTP negotiation saves bandwidth."""
    # Full fetch of v0.5.2
    payload_full = FetchPayload()
    payload_full.add_line(f"want {TEST_TAG_V052} {http_transport.capabilities.as_string()}")
    payload_full.add_delimiter()
    payload_full.add_done()
    
    pack_full = os.path.join(temp_repo_path, "full_v052.pack")
    await http_transport.fetch_pack_v1(payload_full, output_file=pack_full)
    size_full = os.path.getsize(pack_full)
    
    # Incremental fetch v0.5.2 with have v0.5.1
    payload_inc = FetchPayload()
    payload_inc.add_line(f"want {TEST_TAG_V052} {http_transport.capabilities.as_string()}")
    payload_inc.add_delimiter()
    payload_inc.add_have(TEST_TAG_V051)
    payload_inc.add_done()
    
    pack_inc = os.path.join(temp_repo_path, "inc_v052.pack")
    await http_transport.fetch_pack_v1(payload_inc, output_file=pack_inc)
    size_inc = os.path.getsize(pack_inc)
    
    # Verify savings
    assert size_inc < size_full, (
        f"Incremental pack ({size_inc} bytes) should be smaller than "
        f"full pack ({size_full} bytes)"
    )
    
    savings_pct = (1 - size_inc / size_full) * 100
    print(f"Bandwidth savings: {savings_pct:.1f}%")
    assert savings_pct > 50, f"Should save >50% bandwidth (saved {savings_pct:.1f}%)"


@pytest.mark.network
@pytest.mark.trio
async def test_http_v1_vs_v2_compatibility(http_transport, temp_repo_path):
    """Verify that v1 and v2 fetch produce similar results."""
    # Fetch same tag using v1
    payload_v1 = FetchPayload()
    payload_v1.add_line(f"want {TEST_TAG_V051} {http_transport.capabilities.as_string()}")
    payload_v1.add_delimiter()
    payload_v1.add_done()
    
    pack_v1 = os.path.join(temp_repo_path, "compat_v1.pack")
    await http_transport.fetch_pack_v1(payload_v1, output_file=pack_v1)
    size_v1 = os.path.getsize(pack_v1)
    
    # Fetch same tag using v2
    payload_v2 = FetchPayload()
    payload_v2.add_line(f"want {TEST_TAG_V051} {http_transport.capabilities.as_string()}")
    payload_v2.add_delimiter()
    payload_v2.add_done()
    
    pack_v2 = os.path.join(temp_repo_path, "compat_v2.pack")
    await http_transport.fetch_pack_v2(payload_v2, output_file=pack_v2)
    size_v2 = os.path.getsize(pack_v2)
    
    # Sizes should be similar (within 10%)
    ratio = size_v2 / size_v1
    assert 0.9 <= ratio <= 1.1, (
        f"v1 ({size_v1} bytes) and v2 ({size_v2} bytes) should produce "
        f"similar pack sizes (ratio: {ratio:.2f})"
    )


@pytest.mark.network
@pytest.mark.trio
async def test_refs_structure_http(http_transport):
    """Verify HTTP refs have expected structure."""
    refs = await http_transport.fetch_refs()
    
    # Should have various ref types
    has_heads = any(ref.startswith(b'refs/heads/') for ref in refs.values())
    has_tags = any(ref.startswith(b'refs/tags/') for ref in refs.values())
    
    assert has_heads, "Should have branch references"
    assert has_tags, "Should have tag references"
    
    # All refs should be well-formed
    for sha, ref in refs.items():
        assert ref.startswith(b'refs/'), f"Ref should start with 'refs/': {ref}"
        assert len(sha) == 40, f"SHA should be 40 chars: {sha}"
