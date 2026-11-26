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

    def test_get_str_list_value_returns_the_whole_value(self, props):
        """Test getting a comma separated value keeps the commas

        A property is a string on the device, a comma is not a separator of the
        protocol: `zh-CN,en-US` is one value of a locale, and the cpu abilist is
        only a list by convention. get() is what splits a value on commas.
        """
        result = props.get_str('ro.product.cpu.abilist')
        assert result == 'x86_64,arm64-v8a,x86,armeabi-v7a,armeabi'

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


class TestPropsCommaValues:
    """A comma inside a property value is data, not a separator"""

    def test_get_str_keeps_the_commas(self):
        """The raw value is what getprop printed between the brackets"""
        props = Props.from_getprop('[persist.sys.locale]: [zh-CN,en-US]')

        assert props.get_str('persist.sys.locale') == 'zh-CN,en-US'

    def test_get_splits_the_commas(self):
        """get() is the list view of the same value"""
        props = Props.from_getprop('[persist.sys.locale]: [zh-CN,en-US]')

        assert props.get('persist.sys.locale') == ['zh-CN', 'en-US']

    def test_single_value_list(self):
        """A value without a comma is a list of one element"""
        props = Props.from_getprop('[ro.product.brand]: [HUAWEI]')

        assert props.get('ro.product.brand') == ['HUAWEI']
        assert props.get_str('ro.product.brand') == 'HUAWEI'

    def test_empty_value_list(self):
        """An empty value splits to a list with the empty string"""
        props = Props.from_getprop('[ro.hwui.use_vulkan]: []')

        assert props.get('ro.hwui.use_vulkan') == ['']
        assert props.get_str('ro.hwui.use_vulkan') == ''

    def test_props_dict_holds_the_raw_value(self):
        """The parsed property map is the raw value of every property"""
        props = Props.from_getprop('[ro.product.cpu.abilist]: [x86_64,arm64-v8a]')

        assert props.props == {'ro.product.cpu.abilist': 'x86_64,arm64-v8a'}

    def test_very_long_comma_list(self):
        """A device with many abis is not truncated"""
        value = ','.join(['abi%d' % i for i in range(64)])
        props = Props.from_getprop('[ro.product.cpu.abilist]: [%s]' % value)

        assert props.get_str('ro.product.cpu.abilist') == value
        assert props.get('ro.product.cpu.abilist') == value.split(',')


class TestPropsValueShape:
    """Values that are not a bare word"""

    def test_value_with_a_colon(self):
        """A colon after the separator belongs to the value"""
        props = Props.from_getprop('[ro.build.date]: [Thu Feb 20 10:17:10 HKT 2025]')

        assert props.get_str('ro.build.date') == 'Thu Feb 20 10:17:10 HKT 2025'

    def test_value_with_an_equal_sign(self):
        """An equal sign in the value is not a separator either"""
        props = Props.from_getprop('[ro.test.equal]: [a=b;c=d]')

        assert props.get_str('ro.test.equal') == 'a=b;c=d'

    def test_value_with_brackets_inside(self):
        """Only the outer brackets are removed, the rest is data"""
        props = Props.from_getprop('[ro.test.brackets]: [[abc]]')

        assert props.get_str('ro.test.brackets') == '[abc]'

    def test_nested_bracket_of_a_serialized_value(self):
        """A serialized list keeps its own brackets"""
        props = Props.from_getprop('[ro.test.list]: [[1, 2]]')

        assert props.get_str('ro.test.list') == '[1, 2]'

    def test_duplicate_key(self):
        """The last line of a key wins, the way a shell would read it"""
        props = Props.from_getprop('[ro.test.key]: [old]\n[ro.test.key]: [new]')

        assert props.get_str('ro.test.key') == 'new'

    def test_line_without_colon_is_skipped(self):
        """A line of noise does not become a property"""
        props = Props.from_getprop('no separator on this line')

        assert props.props == {}

    def test_empty_property_name(self):
        """A colon at the start of the line makes a property with no name"""
        props = Props.from_getprop(': [value]')

        assert props.get_str('') == 'value'

    def test_windows_line_endings(self):
        """A shell v1 stream of a pty may send CRLF, splitlines reads both"""
        props = Props.from_getprop(b'[ro.a]: [1]\r\n[ro.b]: [2]\r\n')

        assert props.props == {'ro.a': '1', 'ro.b': '2'}

    def test_carriage_return_only(self):
        """The old shells end a line with a bare CR"""
        props = Props.from_getprop('[ro.a]: [1]\r[ro.b]: [2]\r')

        assert props.props == {'ro.a': '1', 'ro.b': '2'}

    def test_last_line_without_newline(self):
        """The last line of the output has no line ending"""
        props = Props.from_getprop('[ro.a]: [1]\n[ro.b]: [2]')

        assert props.get_str('ro.b') == '2'

    def test_invalid_utf8_is_replaced(self):
        """A property with a broken byte does not lose the rest of the output"""
        props = Props.from_getprop(b'[ro.a]: [\xff]\n[ro.b]: [2]')

        assert props.get_str('ro.b') == '2'
        assert props.get_str('ro.a') == '\ufffd'


class TestPropsNumericEdges:
    """Numbers that a device may print"""

    @pytest.mark.parametrize('value, expected', [
        ('0', 0),
        ('32', 32),
        ('-1', -1),
        ('-784056623492961046', -784056623492961046),
        # a float is truncated, the way a cast does it in C
        ('32.9', 32),
        ('0.05', 0),
        ('1e3', 1000),
    ])
    def test_int_values(self, value, expected):
        """get_int parses what an integer property holds"""
        props = Props.from_getprop('[ro.test.value]: [%s]' % value)

        assert props.get_int('ro.test.value') == expected

    @pytest.mark.parametrize('value', [
        '',
        'abc',
        # python does not read the hex prefix int() would refuse as well
        '0x10',
        '32m',
        '1 2',
    ])
    def test_int_values_that_are_not_numbers(self, value):
        """A value that is not a number gives the default"""
        props = Props.from_getprop('[ro.test.value]: [%s]' % value)

        assert props.get_int('ro.test.value') is None
        assert props.get_int('ro.test.value', 7) == 7

    @pytest.mark.parametrize('value, expected', [
        ('0.05', 0.05),
        ('32', 32.0),
        ('-1.5', -1.5),
    ])
    def test_float_values(self, value, expected):
        """get_float parses a float property"""
        props = Props.from_getprop('[ro.test.value]: [%s]' % value)

        assert props.get_float('ro.test.value') == expected

    def test_str_of_a_property_map(self):
        """The representation is short, a props map has thousands of keys"""
        props = Props.from_getprop('[a]: [1]\n[b]: [2]')

        assert str(props) == '<Props count=2>'
        assert repr(props) == '<Props count=2>'


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
