"""
Configuration of the test suite that applies to every test folder.

A test marked `network` leaves the machine: it talks to github.com, to the git
server of git.lyoko.io, or to a real COS bucket. Whether that route exists is a
property of the host, not of the code under test, and a developer laptop behind
a proxy, on an offline network or in a restricted company network would turn a
missing route into a red suite. The runners of the CI do have the route, so the
mark means "runs on CI only": a local run skips it, instead of asking for a flag
on every invocation.

The mark is declared in pyproject.toml. A local run of these tests is still
possible by declaring the machine a CI, the same switch the other CI-only tests
use:

    CI=1 pytest tests/git/fetch
"""
import pytest

from alasio.backport.rich import patch_rich_traceback_extract
from alasio.ext import env

# Shown in the skip line of the report, so it has to say why nothing ran
NETWORK_SKIP_REASON = 'network test, runs on CI only'

# The patch is decided once per process, and the decision reads the version of rich
# from the filesystem that is in place at that moment. A test that replaces the
# filesystem (the in-memory one of the filesystem fixture) cannot answer that read,
# so a first exception formatted inside such a test would decide against the patch
# for the whole worker. The suite applies it here instead, while the disk of the
# process is the one rich was installed on; the logger itself keeps deferring the
# patch, so a process that never formats an exception never pays for it.
patch_rich_traceback_extract()


def pytest_collection_modifyitems(items):
    """
    Skip the network tests when the run is not on CI.

    Args:
        items (list): Collected test items of the session
    """
    if env.CI:
        return
    skip_network = pytest.mark.skip(reason=NETWORK_SKIP_REASON)
    for item in items:
        if item.get_closest_marker('network') is not None:
            item.add_marker(skip_network)
