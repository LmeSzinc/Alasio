"""
Tests for HistoryObj msgpack decoding.
"""
import pytest
from msgspec import DecodeError
from msgspec.msgpack import encode

from alasio.deploy.history.decode_history import HistoryObj, decode_history

SHA1_A = 'a' * 40


class TestDecodeHistory:
    """Decode msgpack bytes to a list of HistoryObj."""

    def test_decode_single(self):
        """A single history object must decode to a list of one HistoryObj."""
        history = HistoryObj(
            version=SHA1_A, author='Author', time=1000, title='Title', detail='Body',
        )
        data = encode([history])
        assert decode_history(data) == [history]

    def test_decode_multiple(self):
        """Multiple history objects must decode in the encoded order."""
        history = [
            HistoryObj(version=SHA1_A, author='Author', time=1000, title='Title', detail='Body'),
            HistoryObj(version='v1.0.0', author='Tagger', time=2000, title='', detail=''),
        ]
        data = encode(history)
        assert decode_history(data) == history

    def test_decode_empty(self):
        """An empty msgpack array must decode to an empty list."""
        assert decode_history(b'\x90') == []

    def test_decode_is_historyobj(self):
        """Decoded items must be HistoryObj instances."""
        data = encode([HistoryObj(
            version=SHA1_A, author='Author', time=1000, title='Title', detail='Body',
        )])
        history = decode_history(data)
        assert isinstance(history[0], HistoryObj)

    def test_decode_error_on_invalid_data(self):
        """Invalid msgpack bytes must raise DecodeError."""
        with pytest.raises(DecodeError):
            decode_history(b'not msgpack data')

    def test_decode_error_on_truncated_data(self):
        """Truncated msgpack bytes must raise DecodeError."""
        data = encode([HistoryObj(
            version=SHA1_A, author='Author', time=1000, title='Title', detail='Body',
        )])
        with pytest.raises(DecodeError):
            decode_history(data[:-1])

    def test_decode_error_on_wrong_type(self):
        """A msgpack map instead of an array must raise DecodeError."""
        with pytest.raises(DecodeError):
            decode_history(encode({'version': SHA1_A}))

    def test_decode_error_on_missing_field(self):
        """A history dict missing required fields must raise DecodeError."""
        with pytest.raises(DecodeError):
            decode_history(encode([{'version': SHA1_A}]))

    def test_decode_error_on_wrong_field_type(self):
        """A history dict with a wrong field type must raise DecodeError."""
        with pytest.raises(DecodeError):
            decode_history(encode([{
                'version': SHA1_A, 'author': 'Author', 'time': '1000',
                'title': 'Title', 'detail': 'Body',
            }]))
