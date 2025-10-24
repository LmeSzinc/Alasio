import pytest

from alasio.assets.model.name import random_id, to_asset_name, validate_asset_name


class TestRandomId:
    """测试 random_id 函数"""

    def test_random_id_format(self):
        """测试随机 ID 的格式"""
        rid = random_id()
        assert len(rid) == 6
        assert rid.isalnum()

    def test_random_id_uniqueness(self):
        """测试随机 ID 的唯一性"""
        ids = [random_id() for _ in range(100)]
        assert len(set(ids)) == 100

    def test_random_id_valid_asset_name(self):
        """测试随机 ID 是有效的资产名称"""
        for _ in range(10):
            rid = random_id()
            assert validate_asset_name(rid) is True


class TestValidateAssetName:
    """测试 validate_asset_name 函数"""

    def test_valid_names(self):
        """测试有效的资产名称"""
        valid_names = ['asset', 'Asset', 'asset_name', 'asset123',
                       'ASSET_123', 'ASSET_for', 'ASSET_print']
        for name in valid_names:
            assert validate_asset_name(name) is True

    def test_empty_string(self):
        """测试空字符串"""
        with pytest.raises(ValueError, match='Asset name cannot be empty'):
            validate_asset_name('')

    def test_starts_with_digit(self):
        """测试以数字开头"""
        with pytest.raises(ValueError, match='Asset name cannot start with digit'):
            validate_asset_name('123asset')

    def test_python_keywords(self):
        """测试 Python 关键字"""
        for kw in ['if', 'for', 'class', 'def']:
            with pytest.raises(ValueError, match='is a Python keyword'):
                validate_asset_name(kw)

    def test_python_builtins(self):
        """测试 Python 内置名称"""
        for builtin in ['print', 'len', 'list', 'dict']:
            with pytest.raises(ValueError, match='is a Python builtin name'):
                validate_asset_name(builtin)


class TestToAssetName:
    """测试 to_asset_name 函数"""

    def test_valid_names_unchanged(self):
        """测试有效名称保持不变"""
        for name in ['asset', 'Asset', 'asset_name', 'asset123']:
            assert to_asset_name(name) == name

    def test_empty_string_returns_asset_random_id(self):
        """测试空字符串返回 ASSET_ + 随机 ID"""
        result = to_asset_name('')
        assert result.startswith('ASSET_')
        assert len(result) == 12
        assert validate_asset_name(result) is True

    def test_consecutive_underscores_collapsed(self):
        """测试连续下划线被合并"""
        assert to_asset_name('asset__name') == 'asset_name'
        assert to_asset_name('a___b') == 'a_b'

    def test_leading_trailing_underscores_stripped(self):
        """测试前后下划线被去除"""
        assert to_asset_name('_asset_') == 'asset'
        assert to_asset_name('__asset__') == 'asset'

    def test_starts_with_digit(self):
        """测试以数字开头添加 ASSET_ 前缀"""
        assert to_asset_name('123asset') == 'ASSET_123asset'
        assert to_asset_name('2024_report') == 'ASSET_2024_report'

    def test_python_keywords(self):
        """测试 Python 关键字添加 ASSET_ 前缀"""
        assert to_asset_name('for') == 'ASSET_for'
        assert to_asset_name('class') == 'ASSET_class'
        assert to_asset_name('if') == 'ASSET_if'

    def test_python_builtins(self):
        """测试 Python 内置函数添加 ASSET_ 前缀"""
        assert to_asset_name('print') == 'ASSET_print'
        assert to_asset_name('list') == 'ASSET_list'
        assert to_asset_name('len') == 'ASSET_len'

    def test_special_characters_replaced(self):
        """测试特殊字符被替换"""
        assert to_asset_name('my-asset') == 'my_asset'
        assert to_asset_name('my.asset') == 'my_asset'
        assert to_asset_name('my asset') == 'my_asset'

    def test_tilde_removal(self):
        """测试波浪号移除"""
        assert to_asset_name('~source') == 'source'
        assert to_asset_name('~123') == 'ASSET_123'
        assert to_asset_name('~for') == 'ASSET_for'


class TestAssetPrefixBehavior:
    """测试 ASSET_ 前缀行为"""

    def test_random_id_with_asset_prefix(self):
        """测试随机 ID 有 ASSET_ 前缀"""
        for input_str in ['', '!!!', '@#$', '___']:
            result = to_asset_name(input_str)
            assert result.startswith('ASSET_')
            assert len(result) == 12

    def test_digit_start_with_asset_prefix(self):
        """测试数字开头有 ASSET_ 前缀"""
        for name in ['1', '123', '2024_report']:
            result = to_asset_name(name)
            assert result.startswith('ASSET_')

    def test_keyword_with_asset_prefix(self):
        """测试关键字有 ASSET_ 前缀"""
        for kw in ['if', 'for', 'class']:
            result = to_asset_name(kw)
            assert result == f'ASSET_{kw}'

    def test_builtin_with_asset_prefix(self):
        """测试内置函数有 ASSET_ 前缀"""
        for builtin in ['print', 'len', 'list']:
            result = to_asset_name(builtin)
            assert result == f'ASSET_{builtin}'


class TestRealWorldExamples:
    """测试真实场景"""

    def test_file_names(self):
        """测试文件名转换"""
        assert to_asset_name('report.pdf') == 'report_pdf'
        assert to_asset_name('2024-report.pdf') == 'ASSET_2024_report_pdf'

    def test_urls(self):
        """测试 URL 转换"""
        assert to_asset_name('api/v1/users') == 'api_v1_users'

    def test_human_readable(self):
        """测试人类可读名称"""
        assert to_asset_name('My Asset') == 'My_Asset'
        assert to_asset_name('2024 Report') == 'ASSET_2024_Report'