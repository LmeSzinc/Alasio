import pytest

from alasio.device.serialstr import SerialStr


class TestSerialStr:
    @pytest.mark.parametrize("input_serial, expected", [
        # Basic strip and space removal
        ("  127.0.0.1:5555  ", "127.0.0.1:5555"),
        ("127.0.0.1 : 5555", "127.0.0.1:5555"),

        # Full-width character replacement
        ("127。0。0。1：5555", "127.0.0.1:5555"),
        ("127,0,0,1:5555", "127.0.0.1:5555"),
        ("127，0，0，1：5555", "127.0.0.1:5555"),

        # Dot to colon replacement
        ("127.0.0.1.5555", "127.0.0.1:5555"),

        # Port range case (5500 < left < 6000 and 16300 < right < 20000)
        ("5555,16384", "127.0.0.1:16384"),  # Note: "16384" will be further processed to "127.0.0.1:16384"

        # Just port number (1000 < port < 65536)
        ("16384", "127.0.0.1:16384"),
        ("5555", "127.0.0.1:5555"),
        ("62001", "127.0.0.1:62001"),

        # Emulator string with regex
        ("夜神模拟器 127.0.0.1:62001", "127.0.0.1:62001"),
        ("MuMu模拟器12127.0.0.1:16384", "127.0.0.1:16384"),

        # Prefix fixes
        ("12127.0.0.1:16384", "127.0.0.1:16384"),
        ("auto127.0.0.1:16384", "127.0.0.1:16384"),
        ("autoemulator-5554", "emulator-5554"),

        # Mixed cases
        ("  auto 127.0.0.1 . 5555  ", "127.0.0.1:5555"),
    ])
    def test_revise_serial(self, input_serial, expected):
        """
        Test revise_serial with various inputs
        """
        # Some expected values might be intermediate, 
        # but the final output should be the fully revised one.
        # For example, "5555,16384" -> "16384" -> "127.0.0.1:16384"
        result = SerialStr.revise_serial(input_serial)
        assert result == expected
        assert isinstance(result, SerialStr)
        assert isinstance(result, str)

    def test_inheritance(self):
        """
        Test that SerialStr behaves like a string
        """
        s = SerialStr("127.0.0.1:5555")
        assert s.startswith("127")
        assert s.endswith("5555")
        assert s.split(":")[1] == "5555"
        assert s + "_test" == "127.0.0.1:5555_test"
