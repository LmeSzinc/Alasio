from tests.backend.import_testing import HeavyImportTest


def import_backend():
    from alasio.backend.app import create_app
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
        # no requests, since we use httpx
        'requests',
        # no device connection
        'adbutils', 'uiautomator2',
    })
    run.run_test(import_backend)
