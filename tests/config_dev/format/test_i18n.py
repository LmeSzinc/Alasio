import pytest

from alasio.config_dev.format.format_i18n import format_i18n, remove_setting_i18n


class TestI18nFormatter:
    """Test cases for i18n formatter function"""

    def test_empty_input(self):
        """Test empty input returns empty string"""
        assert format_i18n(None) == ''
        assert format_i18n('') == ''
        assert format_i18n([]) == ''

    def test_single_line_string(self):
        """Test single line string is stripped and returned as string"""
        assert format_i18n('hello  ') == 'hello'
        assert format_i18n('  world') == '  world'
        assert format_i18n('  test  ') == '  test'

    def test_multi_line_string(self):
        """Test multi-line string is split, stripped and returned as list"""
        input_text = 'line1\nline2  '
        expected = ['line1', 'line2']
        assert format_i18n(input_text) == expected

        input_text = 'line1  \n  line2'
        expected = ['line1', '  line2']
        assert format_i18n(input_text) == expected

    def test_list_input(self):
        """Test list input is processed and returned as list (if multiple items)"""
        input_list = ['line1', 'line2  ']
        expected = ['line1', 'line2']
        assert format_i18n(input_list) == expected

    def test_nested_list(self):
        """Test nested list is flattened"""
        input_list = ['line1', ['line2', 'line3']]
        expected = ['line1', 'line2', 'line3']
        assert format_i18n(input_list) == expected

    def test_mixed_types(self):
        """Test mixed types are converted to string"""
        input_list = ['line1', 123]
        expected = ['line1', '123']
        assert format_i18n(input_list) == expected

    def test_single_item_list_returns_string(self):
        """Test list with single item returns string"""
        input_list = ['hello']
        expected = 'hello'
        assert format_i18n(input_list) == expected

    def test_string_with_empty_lines(self):
        """Test string with empty lines preserves them but strips whitespace"""
        input_text = 'line1\n\nline2'
        expected = ['line1', '', 'line2']
        assert format_i18n(input_text) == expected

    def test_complex_structure(self):
        """Test complex structure with nested lists and newlines"""
        input_data = ['line1', 'line2\nline3', ['line4', 5]]
        expected = ['line1', 'line2', 'line3', 'line4', '5']
        assert format_i18n(input_data) == expected


class TestRemoveSettingI18n:
    """Tests for remove_setting_i18n function"""

    @pytest.mark.parametrize("text, expected", [
        ("主线图设置", "主线图"),
        ("设置UI布局", "UI布局"),
    ])
    def test_chinese_simplified(self, text, expected):
        """Remove simplified Chinese '设置' prefix/suffix"""
        assert remove_setting_i18n(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("依頼設定", "依頼"),
        ("設定ファイル", "ファイル"),
    ])
    def test_japanese(self, text, expected):
        """Remove Japanese '設定' prefix/suffix"""
        assert remove_setting_i18n(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("任務設置", "任務"),
        ("設置功能", "功能"),
    ])
    def test_chinese_traditional(self, text, expected):
        """Remove traditional Chinese '設置' prefix/suffix"""
        assert remove_setting_i18n(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("Opsi Settings", "Opsi"),
        ("Server Setting", "Server"),
        ("My Setting", "My"),
        ("Setting Name", "Name"),
        ("Settings Page", "Page"),
        ("settings page", "page"),
        ("SETTINGS Page", "Page"),
        ("SeTtInGs Page", "Page"),
    ])
    def test_english(self, text, expected):
        """Remove English 'setting/settings' prefix/suffix ignoring case"""
        assert remove_setting_i18n(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("Ajustes de Universo Simulado", "Universo Simulado"),
        ("Ajuste de Pantalla", "Pantalla"),
        ("Cuenta Ajustes", "Cuenta"),
        ("Volumen Ajuste", "Volumen"),
        ("AJUSTES DE UNIVERSO SIMULADO", "UNIVERSO SIMULADO"),
    ])
    def test_spanish_ajustes(self, text, expected):
        """Remove Spanish 'ajustes/ajuste' prefix/suffix ignoring case"""
        assert remove_setting_i18n(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("Opciones de Accesibilidad", "Accesibilidad"),
        ("Opcion de Perfil", "Perfil"),
        ("Impresión Opciones", "Impresión"),
        ("Buscar Opcion", "Buscar"),
    ])
    def test_spanish_opciones(self, text, expected):
        """Remove Spanish 'opciones/opcion' prefix/suffix ignoring case"""
        assert remove_setting_i18n(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("设置", ""),
        ("settings", ""),
        ("setting", ""),
    ])
    def test_only_setting_word(self, text, expected):
        """Return empty string when only the setting word is present"""
        assert remove_setting_i18n(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("主线图", "主线图"),
        ("Hello World", "Hello World"),
        ("", ""),
    ])
    def test_no_match(self, text, expected):
        """Return unchanged when no setting word is present"""
        assert remove_setting_i18n(text) == expected
