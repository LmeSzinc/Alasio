import pytest
from alasio.adb.protocol.props import Props, remove_quote

# Real getprop output from Android device
REAL_GETPROP_OUTPUT = """> adb -s 127.0.0.1:16480 shell getprop
[adb_drop_privileges]: [1]
[apexd.status]: [ready]
[bpf.progs_loaded]: [1]
[build.version.extensions.r]: [1]
[build.version.extensions.s]: [1]
[cache_key.display_info]: [192757500999777558]
[dalvik.vm.heapgrowthlimit]: [192m]
[dalvik.vm.heapmaxfree]: [8m]
[dalvik.vm.heapminfree]: [512k]
[dalvik.vm.heapsize]: [512m]
[debug.tracing.screen_brightness]: [0.05]
[dev.bootcomplete]: [1]
[gsm.network.type]: [HSPA]
[gsm.operator.alpha]: [CHINA MOBILE]
[gsm.operator.iso-country]: [cn]
[gsm.operator.numeric]: [46000]
[ro.hwui.use_vulkan]: []
[ro.build.date]: [Thu Feb 20 10:17:10 HKT 2025]
[ro.build.version.release]: [12]
[ro.build.version.sdk]: [32]
[ro.product.cpu.abilist]: [x86_64,arm64-v8a,x86,armeabi-v7a,armeabi]
[ro.product.cpu.abilist32]: [x86,armeabi-v7a,armeabi]
[ro.product.cpu.abilist64]: [x86_64,arm64-v8a]
[ro.product.brand]: [HUAWEI]
[ro.product.device]: [Draco]
[ro.product.manufacturer]: [HUAWEI]
[ro.product.model]: [DCO-AL00]
[ro.sf.lcd_density]: [240]
[persist.sys.timezone]: [Asia/Shanghai]
"""


class TestRemoveQuote:
    """Test the remove_quote helper function"""

    def test_remove_both_brackets(self):
        """Test removing both opening and closing brackets"""
        assert remove_quote('[value]') == 'value'
        assert remove_quote('[192m]') == '192m'
        assert remove_quote('[x86_64,arm64-v8a]') == 'x86_64,arm64-v8a'

    def test_remove_only_opening_bracket(self):
        """Test removing only opening bracket when closing bracket is missing"""
        assert remove_quote('[value') == 'value'
        assert remove_quote('[test') == 'test'

    def test_remove_only_closing_bracket(self):
        """Test removing only closing bracket when opening bracket is missing"""
        assert remove_quote('value]') == 'value'
        assert remove_quote('test]') == 'test'

    def test_no_brackets(self):
        """Test string without brackets"""
        assert remove_quote('value') == 'value'
        assert remove_quote('') == ''

    def test_empty_brackets(self):
        """Test empty brackets"""
        assert remove_quote('[]') == ''


class TestPropsFromGetprop:
    """Test Props.from_getprop parsing"""

    def test_parse_real_getprop_output(self):
        """Test parsing real getprop output"""
        props = Props.from_getprop(REAL_GETPROP_OUTPUT)

        # Verify Props object is created
        assert isinstance(props, Props)
        assert len(props.props) > 0

        # Verify string representation
        assert 'Props' in str(props)
        assert 'count=' in str(props)

    def test_parse_bytes_input(self):
        """Test parsing getprop output as bytes"""
        props = Props.from_getprop(REAL_GETPROP_OUTPUT.encode('utf-8'))
        assert isinstance(props, Props)
        assert len(props.props) > 0

    def test_parse_simple_property(self):
        """Test parsing simple key-value property"""
        data = "[ro.product.brand]: [HUAWEI]"
        props = Props.from_getprop(data)
        assert props.get_str('ro.product.brand') == 'HUAWEI'

    def test_parse_list_property(self):
        """Test parsing property with comma-separated values"""
        data = "[ro.product.cpu.abilist]: [x86_64,arm64-v8a,x86,armeabi-v7a,armeabi]"
        props = Props.from_getprop(data)
        expected = ['x86_64', 'arm64-v8a', 'x86', 'armeabi-v7a', 'armeabi']
        assert props.get('ro.product.cpu.abilist') == expected

    def test_parse_empty_value(self):
        """Test parsing property with empty value"""
        data = "[ro.hwui.use_vulkan]: []"
        props = Props.from_getprop(data)
        assert props.get('ro.hwui.use_vulkan') == ['']

    def test_parse_numeric_value(self):
        """Test parsing property with numeric value"""
        data = "[ro.build.version.sdk]: [32]"
        props = Props.from_getprop(data)
        assert props.get_str('ro.build.version.sdk') == '32'
        assert props.get_int('ro.build.version.sdk') == 32

    def test_parse_float_value(self):
        """Test parsing property with float value"""
        data = "[debug.tracing.screen_brightness]: [0.05]"
        props = Props.from_getprop(data)
        assert props.get_str('debug.tracing.screen_brightness') == '0.05'
        assert props.get_float('debug.tracing.screen_brightness') == 0.05

    def test_skip_invalid_lines(self):
        """Test skipping lines without colon separator"""
        data = """[ro.product.brand]: [HUAWEI]
Invalid line without colon
Another invalid line
[ro.product.model]: [DCO-AL00]"""
        props = Props.from_getprop(data)
        # Should only parse valid lines
        assert props.get_str('ro.product.brand') == 'HUAWEI'
        assert props.get_str('ro.product.model') == 'DCO-AL00'


class TestPropsGet:
    """Test Props.get method"""

    @pytest.fixture
    def props(self):
        """Create Props instance from real getprop output"""
        return Props.from_getprop(REAL_GETPROP_OUTPUT)

    def test_get_existing_single_value(self, props):
        """Test getting existing property with single value"""
        result = props.get('ro.product.brand')
        assert result == ['HUAWEI']

    def test_get_existing_multiple_values(self, props):
        """Test getting existing property with multiple values"""
        result = props.get('ro.product.cpu.abilist')
        assert len(result) == 5
        assert 'x86_64' in result
        assert 'arm64-v8a' in result

    def test_get_empty_value(self, props):
        """Test getting property with empty value"""
        result = props.get('ro.hwui.use_vulkan')
        assert result == ['']

    def test_get_nonexistent_key(self, props):
        """Test getting non-existent property returns empty list"""
        result = props.get('non.existent.key')
        assert result == []


class TestPropsGetStr:
    """Test Props.get_str method"""

    @pytest.fixture
    def props(self):
        """Create Props instance from real getprop output"""
        return Props.from_getprop(REAL_GETPROP_OUTPUT)

    def test_get_str_existing_value(self, props):
        """Test getting existing string value"""
        assert props.get_str('ro.product.brand') == 'HUAWEI'
        assert props.get_str('ro.product.device') == 'Draco'
        assert props.get_str('gsm.operator.alpha') == 'CHINA MOBILE'

    def test_get_str_numeric_value(self, props):
        """Test getting numeric value as string"""
        assert props.get_str('ro.build.version.sdk') == '32'
        assert props.get_str('ro.sf.lcd_density') == '240'

    def test_get_str_list_value_returns_first(self, props):
        """Test getting list value returns first element"""
        # Should return first element from comma-separated list
        result = props.get_str('ro.product.cpu.abilist')
        assert result == 'x86_64'

    def test_get_str_empty_value(self, props):
        """Test getting empty value returns empty string"""
        result = props.get_str('ro.hwui.use_vulkan')
        assert result == ''

    def test_get_str_nonexistent_key_with_default(self, props):
        """Test getting non-existent key returns default value"""
        assert props.get_str('non.existent.key') is None
        assert props.get_str('non.existent.key', 'default') == 'default'
        assert props.get_str('non.existent.key', '') == ''

    def test_get_str_date_value(self, props):
        """Test getting date string value"""
        result = props.get_str('ro.build.date')
        assert 'Feb 20' in result
        assert '2025' in result


class TestPropsGetInt:
    """Test Props.get_int method"""

    @pytest.fixture
    def props(self):
        """Create Props instance from real getprop output"""
        return Props.from_getprop(REAL_GETPROP_OUTPUT)

    def test_get_int_valid_integer(self, props):
        """Test getting valid integer values"""
        assert props.get_int('ro.build.version.sdk') == 32
        assert props.get_int('ro.sf.lcd_density') == 240
        assert props.get_int('dev.bootcomplete') == 1
        assert props.get_int('bpf.progs_loaded') == 1

    def test_get_int_large_number(self, props):
        """Test getting large integer value"""
        result = props.get_int('cache_key.display_info')
        assert result == 192757500999777558

    def test_get_int_from_float_string(self, props):
        """Test getting int from float string (should truncate)"""
        result = props.get_int('debug.tracing.screen_brightness')
        assert result == 0  # "0.05" -> 0

    def test_get_int_nonexistent_key_with_default(self, props):
        """Test getting non-existent key returns default value"""
        assert props.get_int('non.existent.key') is None
        assert props.get_int('non.existent.key', 0) == 0
        assert props.get_int('non.existent.key', -1) == -1

    def test_get_int_invalid_string_returns_default(self, props):
        """Test getting non-numeric string returns default"""
        # Brand name cannot be converted to int
        assert props.get_int('ro.product.brand') is None
        assert props.get_int('ro.product.brand', 0) == 0

    def test_get_int_empty_value_returns_default(self, props):
        """Test getting empty value returns default"""
        result = props.get_int('ro.hwui.use_vulkan', -1)
        assert result == -1


class TestPropsGetFloat:
    """Test Props.get_float method"""

    @pytest.fixture
    def props(self):
        """Create Props instance from real getprop output"""
        return Props.from_getprop(REAL_GETPROP_OUTPUT)

    def test_get_float_valid_float(self, props):
        """Test getting valid float value"""
        result = props.get_float('debug.tracing.screen_brightness')
        assert result == 0.05

    def test_get_float_from_integer_string(self, props):
        """Test getting float from integer string"""
        result = props.get_float('ro.build.version.sdk')
        assert result == 32.0
        assert isinstance(result, float)

    def test_get_float_nonexistent_key_with_default(self, props):
        """Test getting non-existent key returns default value"""
        assert props.get_float('non.existent.key') is None
        assert props.get_float('non.existent.key', 0.0) == 0.0
        assert props.get_float('non.existent.key', -1.5) == -1.5

    def test_get_float_invalid_string_returns_default(self, props):
        """Test getting non-numeric string returns default"""
        # Brand name cannot be converted to float
        assert props.get_float('ro.product.brand') is None
        assert props.get_float('ro.product.brand', 0.0) == 0.0

    def test_get_float_empty_value_returns_default(self, props):
        """Test getting empty value returns default"""
        result = props.get_float('ro.hwui.use_vulkan', -1.0)
        assert result == -1.0


class TestPropsRealWorldScenarios:
    """Test real-world usage scenarios"""

    @pytest.fixture
    def props(self):
        """Create Props instance from real getprop output"""
        return Props.from_getprop(REAL_GETPROP_OUTPUT)

    def test_check_android_version(self, props):
        """Test checking Android version"""
        # API level 32 = Android 12
        sdk_version = props.get_int('ro.build.version.sdk')
        android_version = props.get_str('ro.build.version.release')

        assert sdk_version == 32
        assert android_version == '12'

    def test_check_device_info(self, props):
        """Test getting device information"""
        brand = props.get_str('ro.product.brand')
        manufacturer = props.get_str('ro.product.manufacturer')
        model = props.get_str('ro.product.model')
        device = props.get_str('ro.product.device')

        assert brand == 'HUAWEI'
        assert manufacturer == 'HUAWEI'
        assert model == 'DCO-AL00'
        assert device == 'Draco'

    def test_check_cpu_abilists(self, props):
        """Test getting CPU ABI lists"""
        # Full ABI list
        abilist = props.get('ro.product.cpu.abilist')
        assert 'x86_64' in abilist
        assert 'arm64-v8a' in abilist
        assert len(abilist) == 5

        # 32-bit ABI list
        abilist32 = props.get('ro.product.cpu.abilist32')
        assert 'x86' in abilist32
        assert 'armeabi-v7a' in abilist32
        assert len(abilist32) == 3

        # 64-bit ABI list
        abilist64 = props.get('ro.product.cpu.abilist64')
        assert 'x86_64' in abilist64
        assert 'arm64-v8a' in abilist64
        assert len(abilist64) == 2

    def test_check_display_density(self, props):
        """Test getting display density"""
        density = props.get_int('ro.sf.lcd_density')
        assert density == 240

    def test_check_network_operator(self, props):
        """Test getting network operator information"""
        operator_alpha = props.get_str('gsm.operator.alpha')
        operator_numeric = props.get_str('gsm.operator.numeric')
        network_type = props.get_str('gsm.network.type')
        country = props.get_str('gsm.operator.iso-country')

        assert operator_alpha == 'CHINA MOBILE'
        assert operator_numeric == '46000'
        assert network_type == 'HSPA'
        assert country == 'cn'

    def test_check_timezone(self, props):
        """Test getting timezone"""
        timezone = props.get_str('persist.sys.timezone')
        assert timezone == 'Asia/Shanghai'

    def test_check_boot_status(self, props):
        """Test checking if device boot is complete"""
        boot_complete = props.get_int('dev.bootcomplete')
        assert boot_complete == 1


class TestPropsEdgeCases:
    """Test edge cases and error handling"""

    def test_empty_input(self):
        """Test parsing empty input"""
        props = Props.from_getprop('')
        assert len(props.props) == 0
        assert props.get('any.key') == []

    def test_malformed_input(self):
        """Test parsing malformed input"""
        data = """
        This is not valid getprop output
        [key without value]
        [valid.key]: [valid.value]
        random text here
        """
        props = Props.from_getprop(data)
        # Should parse only valid lines
        assert props.get_str('valid.key') == 'valid.value'

    def test_unicode_in_values(self):
        """Test handling unicode characters"""
        data = "[ro.test.unicode]: [测试值]"
        props = Props.from_getprop(data)
        assert props.get_str('ro.test.unicode') == '测试值'

    def test_special_characters_in_values(self):
        """Test handling special characters"""
        data = "[ro.test.special]: [value-with_special.chars@123]"
        props = Props.from_getprop(data)
        assert props.get_str('ro.test.special') == 'value-with_special.chars@123'

    def test_very_long_value(self):
        """Test handling very long values"""
        long_value = 'a' * 1000
        data = f"[ro.test.long]: [{long_value}]"
        props = Props.from_getprop(data)
        assert props.get_str('ro.test.long') == long_value

    def test_whitespace_handling(self):
        """Test handling whitespace in input"""
        data = """
        [ro.test.spaces]   :   [value with spaces]
        [ ro.test.leading]: [value]
        [ro.test.trailing ]   : [value]
        """
        props = Props.from_getprop(data)
        # Leading/trailing spaces in keys should be stripped
        assert props.get_str('ro.test.spaces') == 'value with spaces'
