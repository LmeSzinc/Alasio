"""
Fixtures of the deploy pack tests, the tests of alasio/deploy/pack.

The pack fixtures of the tests (the mock website, MockServerFile, the fake
mirror table, the upgrade scenarios, ...) are built with the encoder
(alasio.deploy_dev.pack), they are shared with the deploy_dev pack tests and
live in tests/deploy_dev/pack/conftest.py, the way tests/deploy/simple_pip
imports its helpers.

The fixtures in scope of this folder are re-exported here:

- app_folder: env.PROJECT_ROOT pointed at the in-memory fake filesystem
- cache: a fresh pack cache for the test, see _pack_cache
- _posix_exec_bits: the mode matching runs its POSIX branch on every host
"""
from tests.deploy_dev.pack.conftest import _posix_exec_bits, app_folder, cache  # noqa: F401
