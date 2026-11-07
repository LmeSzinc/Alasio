"""
Fixtures of the COS store tests.

The store is exercised against a fake COS service that runs as a starlette app
in the event loop of the test (see fake_cos.py, over httpx2.ASGITransport): no
socket is bound, so the workers that run these files in parallel never compete
for a port, and the client parses a real answer instead of a canned one.
"""

import pytest

from tests.deploy_dev.store.cos.fake_cos import FakeCos


@pytest.fixture
async def fake_cos():
    """
    The fake COS service of one test.

    Yields:
        FakeCos: The service, its clients are closed when the test ends
    """
    service = FakeCos()
    try:
        yield service
    finally:
        await service.aclose()
