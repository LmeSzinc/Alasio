"""
Tests for extract_last_task in alasio.logger.error

Scenarios covered:
- Marker at the start of file
- Marker in the middle of file
- Multiple tasks: extracts only the last task
- Marker in the last reverse-read block (first iteration)
- Marker overlaps 2 read blocks (straddles chunk boundary)
- No marker in file (full file output)
- Empty file (empty output)
- Single line without marker
- Single byte without marker
- Marker right at end of file (no trailing content)
- Header-only: hr0 with no trailing log lines
- Back-to-back markers: two task headers adjacent
- CRLF line endings
- block_size smaller than file (multi-iteration reverse search)
"""
import io

import pytest

from alasio.logger.error import extract_last_task


def _make_header(title):
    """
    Create hr0-style task header bytes matching the marker regex.

    Uses the same centering logic as logger.hr0():
        interior = max(98, len(title) + 10)
        edge = f'+{"=" * interior}+'
        hr = f'|{f" {title} ".center(interior, " ")}|'

    Args:
        title (str): Title text (ASCII)

    Returns:
        bytes: Three-line header with Unix line endings
    """
    title_upper = title.upper()
    interior = max(98, len(title_upper) + 10)
    edge = f'+{"=" * interior}+\n'
    hr_line = f'|{f" {title_upper} ".center(interior, " ")}|\n'
    return (edge + hr_line + edge).encode('ascii')


def _make_log(msg, level='INFO'):
    """
    Create a log line in standard format.

    Args:
        msg (str): Log message
        level (str): Log level

    Returns:
        bytes: Log line with Unix line ending
    """
    return f'2026-06-25 12:00:00.000 | {level} | {msg}\n'.encode()


class TestExtractLastTask:
    """Test suite for extract_last_task."""

    # ------------------------------------------------------------------
    # Core scenarios
    # ------------------------------------------------------------------

    @pytest.mark.parametrize('block_size', [4096, 262144])
    def test_marker_at_file_start(self, block_size):
        """
        Marker is at the very beginning of the file.

        The reverse search should find the marker in the first read chunk
        (or the only read chunk) and output from the second byte onward.
        """
        content = (
            _make_header('LOGIN')
            + _make_log('login start')
            + _make_log('login success')
        )

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target, block_size=block_size)
        result = target.getvalue()

        # Expected: content from second byte (after skipping first '+') to end
        expected = content[1:]
        assert result == expected

    def test_marker_in_middle(self):
        """
        Marker is in the middle of the file, preceded by preamble data.

        The reverse search should skip the preamble and find the task header.
        """
        preamble = b'line 1\nline 2\nline 3\n'
        content = (
            preamble
            + _make_header('COMBAT')
            + _make_log('combat start')
            + _make_log('combat end')
        )

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target)
        result = target.getvalue()

        # Expected: task from marker to end, skipping first byte
        expected = content[len(preamble) + 1:]
        assert result == expected

    def test_multiple_tasks_extracts_last(self):
        """
        Multiple task sections in the file: only the LAST task is extracted.

        The reverse search finds the last occurrence of the marker pattern.
        """
        earlier_task = (
            _make_header('LOGIN')
            + _make_log('login done')
        )
        last_task = (
            _make_header('COMMISSION')
            + _make_log('commission start')
            + _make_log('commission end')
        )
        content = earlier_task + last_task

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target)
        result = target.getvalue()

        expected = last_task[1:]
        assert result == expected

    def test_back_to_back_markers(self):
        """
        Two task headers are adjacent with no log lines between them.

        The reverse search must identify the LAST marker correctly
        even when the third line of header A coincides with the first
        line of header B being the same `+=====...=====+` pattern.
        """
        header_a = _make_header('FIRST')
        header_b = _make_header('SECOND')
        log_line = _make_log('second task')
        content = header_a + header_b + log_line

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target)
        result = target.getvalue()

        # Last marker is header_b, output from its second byte
        expected = header_b[1:] + log_line
        assert result == expected

    def test_marker_at_last_block(self):
        """
        Marker is in the last reverse-read block (first iteration).

        Use a small block_size and a large file so the marker lives
        entirely within the first (tail-end) chunk read in reverse;
        no overlap or backward-walking needed.
        """
        block_size = 4096

        # filler: 20000 bytes before the marker
        # first reverse-read spans from (filesize - block_size) aligned
        # down to EOF: (20348 - 4096)//4096*4096 = 12288.
        # Marker at byte ~20000 is within [12288, 20348) -> found in iter 1.
        filler = b'x\n' * 10000             # 20000 bytes
        marker = _make_header('LAST_BLOCK')  # 306 bytes
        log_line = _make_log('last')         # ~42 bytes
        content = filler + marker + log_line

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target, block_size=block_size)
        result = target.getvalue()

        expected = content[len(filler) + 1:]
        assert result == expected

    # ------------------------------------------------------------------
    # Boundary / edge cases
    # ------------------------------------------------------------------

    def test_no_marker_in_file(self):
        """
        File has no hr0 marker pattern: entire file should be output.

        When the reverse search exhausts all chunks without a match,
        every chunk is accumulated and written in order.
        """
        content = (
            b'some log line without header\n'
            b'another ordinary line\n'
            b'third line\n'
        )

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target)
        result = target.getvalue()

        assert result == content

    def test_empty_file(self):
        """
        Empty file produces empty output.

        The while loop condition prevents any read, and both
        last_chunk and last_read_end remain at initial values.
        """
        target = io.BytesIO()
        extract_last_task(io.BytesIO(b''), target)
        result = target.getvalue()

        assert result == b''

    def test_single_byte_no_marker(self):
        """
        Single byte file with no marker pattern.

        Verify the function handles an input smaller than any
        meaningful pattern without crashing.
        """
        for content in [b'+', b'x', b'\n']:
            target = io.BytesIO()
            extract_last_task(io.BytesIO(content), target)
            result = target.getvalue()

            assert result == content

    def test_single_line_no_marker(self):
        """
        Single non-marker line outputs the same line unchanged.
        """
        content = b'2026-06-25 12:00:00.000 | INFO | single line\n'

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target)
        result = target.getvalue()

        assert result == content

    def test_header_only(self):
        """
        Only an hr0 header with NO trailing log lines.

        Verifies the forward-read phase correctly handles
        last_read_end == file_size (nothing more to read).
        """
        content = _make_header('HEADER_ONLY')

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target)
        result = target.getvalue()

        expected = content[1:]
        assert result == expected

    def test_marker_right_at_end_of_file(self):
        """
        Marker is at the very end with no trailing log content.

        Verifies the forward-read phase handles the case where
        last_read_end == file_size (no additional bytes to read).
        """
        preamble = b'preamble line\n'
        content = preamble + _make_header('END')

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target)
        result = target.getvalue()

        expected = content[len(preamble) + 1:]
        assert result == expected

    def test_crlf_line_endings(self):
        """
        File with CRLF (\r\n) line endings: the regex handles both.

        The marker regex uses \r?\n to accept both Unix and Windows
        line endings.
        """
        edge = b'+' + b'=' * 98 + b'+\r\n'
        hr_line = b'|' + b' ' * 47 + b'TEST' + b' ' * 47 + b'|\r\n'
        header = edge + hr_line + edge
        log = b'2026-06-25 12:00:00.000 | INFO | test\r\n'
        content = header + log

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target)
        result = target.getvalue()

        expected = content[1:]
        assert result == expected

    # ------------------------------------------------------------------
    # Reverse-search block-boundary scenarios
    # ------------------------------------------------------------------

    def test_overlap_2_blocks(self):
        """
        Marker straddles the boundary between two reverse-read blocks.

        The marker starts before the first reverse-read chunk (iteration 1)
        and is found in iteration 2 thanks to the overlap buffer.
        """
        block_size = 4096

        # Build a file where the marker straddles the [12288, 16384) boundary:
        #
        #   bytes [0, 12270):     non-matching filler (x\n repeated)
        #   bytes [12270, 12576): marker header (306 bytes)
        #   bytes [12576, ...):   log lines
        #
        # Iteration 1 reads from 12288 (aligned) to EOF (~12618).
        # Marker starts at 12270 (< 12288) -> not found in iteration 1.
        # leftover carries [12288, 12618) into iteration 2.
        # Iteration 2: chunk=[8192, 12288), search_buffer=[8192, 12618).
        # Marker at [12270, 12576) is fully in search_buffer -> found.
        filler = b'x\n' * 6135            # 12270 bytes
        marker = _make_header('OVERLAP')  # 306 bytes
        log_line = _make_log('overlap')   # ~42 bytes
        content = filler + marker + log_line

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target, block_size=block_size)
        result = target.getvalue()

        expected = content[len(filler) + 1:]
        assert result == expected

    def test_block_size_smaller_than_file(self):
        """
        block_size smaller than file; marker found after multiple reverse
        iterations that don't straddle a boundary.
        """
        block_size = 4096

        # Fill enough data so the reverse search requires multiple iterations
        filler = b'line\n' * 5000         # 30000 bytes
        marker = _make_header('DEEP')
        log_line = _make_log('deep')
        content = filler + marker + log_line

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target, block_size=block_size)
        result = target.getvalue()

        expected = content[len(filler) + 1:]
        assert result == expected

    def test_overlap_with_crlf(self):
        """
        Marker with CRLF line endings straddles a block boundary.

        Combines the overlap scenario with \r\n line endings.
        """
        block_size = 4096

        # Build CRLF header manually for precise placement
        interior = 98
        edge = b'+' + b'=' * interior + b'+\r\n'
        hr_line = b'|' + b' ' * 47 + b'CRLF' + b' ' * 47 + b'|\r\n'
        marker = edge + hr_line + edge   # 306 bytes

        # Filler ends at byte 12270 so marker starts straddling [12288, ...)
        filler = b'x\r\n' * 4090         # 12270 bytes
        log_line = b'2026-06-25 12:00:00.000 | INFO | crlf\r\n'
        content = filler + marker + log_line

        target = io.BytesIO()
        extract_last_task(io.BytesIO(content), target, block_size=block_size)
        result = target.getvalue()

        expected = content[len(filler) + 1:]
        assert result == expected
