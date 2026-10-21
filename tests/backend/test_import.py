import sys

from tests.backend.import_testing import HeavyImportTest

# trio imports asyncio.windows_utils for the pipes of a subprocess, so every
# windows process that imports trio imports asyncio as well: the check can only
# hold on the other platforms
FORBIDDEN_ASYNC = {'asyncio'} if sys.platform != 'win32' else set()


def import_backend():
    # backend startup: the server layer (the entry module) is imported first,
    # the ASGI app is imported lazily by serve_app
    from alasio.backend.app.app import create_app
    from alasio.backend.app.asgi import run  # noqa: F401
    create_app()


def test_backend_no_heavy_import():
    run = HeavyImportTest({
        # no image processing
        'numpy', 'scipy', 'cv2', 'av', 'matplotlib',
        # no data processing
        'pandas',
        # no machine learning
        'torch', 'keras', 'tensorflow', 'mxnet', 'onnxruntime', 'sklearn',
        # no other web frameworks, since we use starlette
        'flask', 'tornado', 'django',
        # no pydantic, since we use msgspec
        'pydantic',
        # no requests, since we use httpx2
        'requests',
        # no device connection
        'adbutils', 'uiautomator2',
        # no other async library, since we use trio
        *FORBIDDEN_ASYNC, 'greenlet',
    })
    run.run_test(import_backend)
