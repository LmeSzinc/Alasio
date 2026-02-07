from alasio.logger import logger


def test_mock_capture_writer():
    """
    Test that mock_capture_writer correctly captures all types of logs
    without writing to actual stdout, files, or backend.
    """
    with logger.mock_capture_writer() as capture:
        # 1. Test basic logging
        logger.info("Hello Info")
        assert capture.fd.any_contains("Hello Info")
        assert capture.stdout.any_contains("Hello Info")
        assert any(log['l'] == 'INFO' and log['m'] == 'Hello Info' for log in capture.backend.logs)
        capture.clear()

        # 2. Test raw logging
        logger.raw("Hello Raw")
        assert capture.fd.any_contains("Hello Raw\n")
        assert capture.stdout.any_contains("Hello Raw\n")
        assert any(log['r'] == 1 and log['m'] == 'Hello Raw' for log in capture.backend.logs)
        capture.clear()

        # 3. Test exception logging
        try:
            raise ValueError("Test Error")
        except ValueError:
            logger.exception("An exception occurred")

        # Verify exception is in text output
        assert capture.fd.any_contains("ValueError: Test Error")
        # Verify exception is in backend log
        assert capture.backend.any_contains("ValueError: Test Error")
        capture.clear()

        logger.info("Fresh log")
        assert capture.fd.any_contains("Fresh log")
        assert len(capture.backend.logs) == 1


def test_mock_capture_writer_restoration():
    """
    Test that the original writer is restored after the context manager exits.
    """
    original_writer = logger._writer

    with logger.mock_capture_writer() as capture:
        assert logger._writer is capture
        assert logger._writer is not original_writer

    assert logger._writer is original_writer
