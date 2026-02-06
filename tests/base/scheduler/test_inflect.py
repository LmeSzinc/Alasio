import pytest

from alasio.base.scheduler.inflect import Inflection


class TestInflectionBasic:
    """基础功能测试"""

    def test_simple_camel_case(self):
        inf = Inflection.from_string("camelCase")
        assert inf.to_snake_case() == "camel_case"
        assert inf.to_kebab_case() == "camel-case"
        assert inf.to_pascal_case() == "CamelCase"

    def test_simple_pascal_case(self):
        inf = Inflection.from_string("PascalCase")
        assert inf.to_snake_case() == "pascal_case"
        assert inf.to_kebab_case() == "pascal-case"
        assert inf.to_camel_case() == "pascalCase"

    def test_simple_snake_case(self):
        inf = Inflection.from_string("snake_case")
        assert inf.to_kebab_case() == "snake-case"
        assert inf.to_pascal_case() == "SnakeCase"
        assert inf.to_camel_case() == "snakeCase"

    def test_simple_kebab_case(self):
        inf = Inflection.from_string("kebab-case")
        assert inf.to_snake_case() == "kebab_case"
        assert inf.to_pascal_case() == "KebabCase"


class TestInflectionEdgeCases:
    """边界情况和攻击性测试"""

    def test_empty_string(self):
        inf = Inflection.from_string("")
        assert inf.to_snake_case() == ""
        assert inf.to_kebab_case() == ""
        assert inf.to_pascal_case() == ""
        assert inf.to_camel_case() == ""

    def test_none_handling(self):
        """测试 None 输入 - 返回空 Inflection 对象"""
        inf = Inflection.from_string(None)
        assert inf.to_snake_case() == ""
        assert inf.to_kebab_case() == ""

    def test_single_character(self):
        inf = Inflection.from_string("a")
        assert inf.to_snake_case() == "a"
        assert inf.to_kebab_case() == "a"
        assert inf.to_pascal_case() == "A"
        assert inf.to_camel_case() == "a"

    def test_single_uppercase_character(self):
        inf = Inflection.from_string("A")
        assert inf.to_snake_case() == "a"
        assert inf.to_kebab_case() == "a"
        assert inf.to_pascal_case() == "A"
        assert inf.to_camel_case() == "a"

    def test_only_special_characters(self):
        inf = Inflection.from_string("___---...")
        assert inf.to_snake_case() == ""
        assert inf.to_kebab_case() == ""
        assert inf.to_pascal_case() == ""

    def test_only_spaces(self):
        inf = Inflection.from_string("     ")
        assert inf.to_snake_case() == ""
        assert inf.to_kebab_case() == ""

    def test_mixed_separators(self):
        inf = Inflection.from_string("my-crazy_variable.name")
        assert inf.to_snake_case() == "my_crazy_variable_name"
        assert inf.to_kebab_case() == "my-crazy-variable-name"
        assert inf.to_pascal_case() == "MyCrazyVariableName"


class TestInflectionApostrophes:
    """撇号和特殊字符测试"""

    def test_apostrophe_removal(self):
        inf = Inflection.from_string("Bob's")
        assert inf.to_snake_case() == "bobs"
        assert inf.to_kebab_case() == "bobs"
        assert inf.to_pascal_case() == "Bobs"

    def test_smart_apostrophe(self):
        inf = Inflection.from_string("user's")
        assert inf.to_snake_case() == "users"

    def test_multiple_apostrophes(self):
        inf = Inflection.from_string("it's'a'me")
        assert inf.to_snake_case() == "itsame"


class TestInflectionAcronyms:
    """首字母缩写词测试"""

    def test_http_server(self):
        inf = Inflection.from_string("HTTPServer")
        assert inf.to_snake_case() == "http_server"
        assert inf.to_kebab_case() == "http-server"

    def test_xml_parser(self):
        inf = Inflection.from_string("XMLParser")
        assert inf.to_snake_case() == "xml_parser"

    def test_io_error(self):
        inf = Inflection.from_string("IOError")
        assert inf.to_snake_case() == "io_error"

    def test_all_caps(self):
        inf = Inflection.from_string("HTTP")
        assert inf.to_snake_case() == "http"
        assert inf.to_pascal_case() == "Http"

    def test_caps_with_number(self):
        inf = Inflection.from_string("HTML5Parser")
        assert inf.to_snake_case() == "html5_parser"


class TestInflectionNumbers:
    """数字处理测试"""

    def test_number_at_end(self):
        inf = Inflection.from_string("variable1")
        assert inf.to_snake_case() == "variable1"
        assert inf.to_pascal_case() == "Variable1"

    def test_number_in_middle(self):
        inf = Inflection.from_string("var2name")
        assert inf.to_snake_case() == "var2name"

    def test_number_before_capital(self):
        inf = Inflection.from_string("var1Name")
        assert inf.to_snake_case() == "var1_name"

    def test_only_numbers(self):
        inf = Inflection.from_string("12345")
        assert inf.to_snake_case() == "12345"
        assert inf.to_pascal_case() == "12345"

    def test_mixed_numbers_letters(self):
        inf = Inflection.from_string("a1b2c3")
        assert inf.to_snake_case() == "a1b2c3"


class TestInflectionUnicode:
    """Unicode 和特殊字符测试"""

    def test_unicode_characters(self):
        inf = Inflection.from_string("café")
        # 非 ASCII 字符会被转换为下划线
        assert "_" in inf.to_snake_case() or inf.to_snake_case() == "caf"

    def test_chinese_characters(self):
        inf = Inflection.from_string("变量名")
        # 应该被过滤掉或转换为下划线
        result = inf.to_snake_case()
        assert result == "" or "_" in result

    def test_emoji(self):
        inf = Inflection.from_string("my😀variable")
        result = inf.to_snake_case()
        assert "my" in result
        assert "variable" in result


class TestInflectionConsecutiveSeparators:
    """连续分隔符测试"""

    def test_multiple_underscores(self):
        inf = Inflection.from_string("my___internal___var")
        assert inf.to_snake_case() == "my_internal_var"
        assert inf.to_kebab_case() == "my-internal-var"

    def test_multiple_hyphens(self):
        inf = Inflection.from_string("my---special---name")
        assert inf.to_snake_case() == "my_special_name"

    def test_mixed_consecutive_separators(self):
        inf = Inflection.from_string("my_-_crazy_-_name")
        assert inf.to_snake_case() == "my_crazy_name"


class TestInflectionLongStrings:
    """长字符串和性能测试"""

    def test_very_long_string(self):
        long_name = "this" + "VeryLongName" * 100
        inf = Inflection.from_string(long_name)
        result = inf.to_snake_case()
        assert result.startswith("this")
        assert "_very_long_name" in result

    def test_many_words(self):
        words = "_".join([f"word{i}" for i in range(100)])
        inf = Inflection.from_string(words)
        result = inf.to_snake_case()
        assert result.count("_") == 99


class TestInflectionDirectInstantiation:
    """直接实例化测试"""

    def test_direct_init_empty_list(self):
        inf = Inflection([])
        assert inf.to_snake_case() == ""
        assert inf.to_camel_case() == ""

    def test_direct_init_with_words(self):
        inf = Inflection(["hello", "world"])
        assert inf.to_snake_case() == "hello_world"
        assert inf.to_pascal_case() == "HelloWorld"
        assert inf.to_camel_case() == "helloWorld"

    def test_direct_init_mixed_case(self):
        inf = Inflection(["HELLO", "WoRlD"])
        assert inf.to_snake_case() == "hello_world"
        assert inf.to_pascal_case() == "HelloWorld"


class TestInflectionWeirdCases:
    """奇怪和意外的情况"""

    def test_leading_underscore(self):
        inf = Inflection.from_string("_private")
        assert inf.to_snake_case() == "private"

    def test_trailing_underscore(self):
        inf = Inflection.from_string("private_")
        assert inf.to_snake_case() == "private"

    def test_leading_hyphen(self):
        inf = Inflection.from_string("-negative")
        assert inf.to_kebab_case() == "negative"

    def test_mixed_case_preservation(self):
        inf = Inflection.from_string("WeIrDCaSe")
        result = inf.to_snake_case()
        # 每个大写字母都被视为新单词的开始
        assert result == "we_ir_d_ca_se"

    def test_all_separators_mixed(self):
        inf = Inflection.from_string("my_var-name.value space")
        assert inf.to_snake_case() == "my_var_name_value_space"

    def test_consecutive_capitals(self):
        inf = Inflection.from_string("ALLCAPS")
        assert inf.to_snake_case() == "allcaps"
        assert inf.to_pascal_case() == "Allcaps"

    def test_alternating_case(self):
        inf = Inflection.from_string("AlTeRnAtInG")
        result = inf.to_snake_case()
        # 应该将每个大写字母视为新单词的开始
        assert "_" in result or result == "alternating"


class TestInflectionRealWorldExamples:
    """真实世界的例子"""

    def test_rest_api_endpoint(self):
        inf = Inflection.from_string("getUserProfile")
        assert inf.to_snake_case() == "get_user_profile"
        assert inf.to_kebab_case() == "get-user-profile"

    def test_database_column(self):
        inf = Inflection.from_string("user_created_at")
        assert inf.to_pascal_case() == "UserCreatedAt"
        assert inf.to_camel_case() == "userCreatedAt"

    def test_css_class_name(self):
        inf = Inflection.from_string("btn-primary-large")
        assert inf.to_snake_case() == "btn_primary_large"
        assert inf.to_camel_case() == "btnPrimaryLarge"

    def test_constant_name(self):
        inf = Inflection.from_string("MAX_RETRY_COUNT")
        assert inf.to_camel_case() == "maxRetryCount"
        assert inf.to_kebab_case() == "max-retry-count"

    def test_file_name(self):
        inf = Inflection.from_string("my-component.test.js")
        assert inf.to_snake_case() == "my_component_test_js"
        assert inf.to_pascal_case() == "MyComponentTestJs"


class TestInflectionTypeErrors:
    """类型错误测试"""

    def test_integer_input(self):
        with pytest.raises((TypeError, AttributeError)):
            Inflection.from_string(12345)

    def test_list_input(self):
        with pytest.raises((TypeError, AttributeError)):
            Inflection.from_string(["hello", "world"])

    def test_dict_input(self):
        with pytest.raises((TypeError, AttributeError)):
            Inflection.from_string({"key": "value"})

    def test_direct_init_non_list(self):
        """测试直接用字符串初始化 - 字符串是可迭代的，会被当作字符列表"""
        inf = Inflection("hello")
        # 字符串会被迭代为单个字符 ['h', 'e', 'l', 'l', 'o']
        result = inf.to_snake_case()
        assert result == "h_e_l_l_o"


class TestInflectionIdempotency:
    """幂等性测试 - 多次转换应该产生相同结果"""

    def test_snake_case_idempotency(self):
        original = "myVariableName"
        inf1 = Inflection.from_string(original)
        snake1 = inf1.to_snake_case()

        inf2 = Inflection.from_string(snake1)
        snake2 = inf2.to_snake_case()

        assert snake1 == snake2

    def test_kebab_case_idempotency(self):
        original = "MyVariableName"
        inf1 = Inflection.from_string(original)
        kebab1 = inf1.to_kebab_case()

        inf2 = Inflection.from_string(kebab1)
        kebab2 = inf2.to_kebab_case()

        assert kebab1 == kebab2

    def test_round_trip_conversion(self):
        original = "myVariableName"
        inf = Inflection.from_string(original)

        # 转换到 snake_case
        snake = inf.to_snake_case()
        assert snake == "my_variable_name"

        # 再转回 camelCase
        inf2 = Inflection.from_string(snake)
        camel = inf2.to_camel_case()

        # 应该得到相同的 camelCase 形式
        assert camel == "myVariableName"
