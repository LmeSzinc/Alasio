from typing import get_args

import msgspec
import pytest

from alasio.ext.backport import process_cpu_count, removeprefix, removesuffix, to_literal


class TestRemovePrefix:
    def test_removeprefix(self):
        # Normal case
        assert removeprefix("prefix_text", "prefix_") == "text"
        # No match
        assert removeprefix("text", "prefix_") == "text"
        # Empty prefix
        assert removeprefix("prefix_text", "") == "prefix_text"
        # Partial match at start
        assert removeprefix("aaa", "aa") == "a"
        # Prefix longer than string
        assert removeprefix("aaa", "aaaa") == "aaa"
        # Match but not at start
        assert removeprefix("text_prefix", "prefix") == "text_prefix"
        # Empty input string
        assert removeprefix("", "prefix") == ""
        assert removeprefix("", "") == ""

    def test_removeprefix_bytes(self):
        # Normal case
        assert removeprefix(b"prefix_text", b"prefix_") == b"text"
        # No match
        assert removeprefix(b"text", b"prefix_") == b"text"
        # Empty prefix
        assert removeprefix(b"prefix_text", b"") == b"prefix_text"
        # Partial match at start
        assert removeprefix(b"aaa", b"aa") == b"a"
        # Prefix longer than string
        assert removeprefix(b"aaa", b"aaaa") == b"aaa"
        # Match but not at start
        assert removeprefix(b"text_prefix", b"prefix") == b"text_prefix"
        # Empty input string
        assert removeprefix(b"", b"prefix") == b""
        assert removeprefix(b"", b"") == b""


class TestRemoveSuffix:
    def test_removesuffix(self):
        # Normal case
        assert removesuffix("text_suffix", "_suffix") == "text"
        # No match
        assert removesuffix("text", "_suffix") == "text"
        # Empty suffix
        assert removesuffix("text_suffix", "") == "text_suffix"
        # Partial match at end
        assert removesuffix("aaa", "aa") == "a"
        # Suffix longer than string
        assert removesuffix("aaa", "aaaa") == "aaa"
        # Match but not at end
        assert removesuffix("suffix_text", "suffix") == "suffix_text"
        # Empty input string
        assert removesuffix("", "suffix") == ""
        assert removesuffix("", "") == ""

    def test_removesuffix_bytes(self):
        # Normal case
        assert removesuffix(b"text_suffix", b"_suffix") == b"text"
        # No match
        assert removesuffix(b"text", b"_suffix") == b"text"
        # Empty suffix
        assert removesuffix(b"text_suffix", b"") == b"text_suffix"
        # Partial match at end
        assert removesuffix(b"aaa", b"aa") == b"a"
        # Suffix longer than string
        assert removesuffix(b"aaa", b"aaaa") == b"aaa"
        # Match but not at end
        assert removesuffix(b"suffix_text", b"suffix") == b"suffix_text"
        # Empty input string
        assert removesuffix(b"", b"suffix") == b""
        assert removesuffix(b"", b"") == b""


class TestToLiteral:
    def test_to_literal_content(self):
        """
        Test if the resulting Literal can be traversed for correct content
        """
        items = ["zh", "en", "ja", "kr"]
        lang_t = to_literal(items)
        # Use get_args to verify the content of the Literal type
        assert get_args(lang_t) == ("zh", "en", "ja", "kr")

    def test_to_literal_msgspec(self):
        """
        Test if the resulting Literal can be used as a msgspec struct field annotation
        """
        items = ["zh", "en"]
        lang_t = to_literal(items)

        class UserConfig(msgspec.Struct):
            lang: lang_t

        # Test valid input
        data_zh = msgspec.json.decode(b'{"lang": "zh"}', type=UserConfig)
        assert data_zh.lang == "zh"
        data_en = msgspec.json.decode(b'{"lang": "en"}', type=UserConfig)
        assert data_en.lang == "en"

        # Test invalid input
        with pytest.raises(msgspec.ValidationError):
            msgspec.json.decode(b'{"lang": "ja"}', type=UserConfig)

        with pytest.raises(msgspec.ValidationError):
            msgspec.json.decode(b'{"lang": 123}', type=UserConfig)


class TestProcessCpuCount:
    def test_process_cpu_count_call(self):
        """
        Ensure process_cpu_count runs without raising exceptions
        """
        count = process_cpu_count()
        assert count is None or isinstance(count, int)
