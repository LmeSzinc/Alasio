from alasio.config_dev.parse.parse_store import ParseStore
from alasio.ext.file.jsonfile import json_dumps_custom_indent


class TestNamesOnly:
    """The i18n of the store carries the name of a row only"""

    def test_help_is_dropped(self):
        """The help of a row is not written to the store i18n"""
        data = {
            'DashboardAmount': {
                '_info': {
                    'zh-CN': {'name': '看板数值', 'help': '记录一个非负整数'},
                    'en-US': {'name': 'Dashboard Amount', 'help': 'Records a non-negative integer'},
                },
                'Value': {
                    'zh-CN': {'name': '数量', 'help': '当前记录的数值'},
                    'en-US': {'name': 'Amount', 'help': ''},
                },
            }
        }
        result = json_dumps_custom_indent(ParseStore._names_only(data)).decode('utf-8')
        assert result == """\
{
  "DashboardAmount": {
    "_info": {
      "zh-CN": {"name": "看板数值"},
      "en-US": {"name": "Dashboard Amount"}
    },
    "Value": {
      "zh-CN": {"name": "数量"},
      "en-US": {"name": "Amount"}
    }
  }
}"""

    def test_row_without_name(self):
        """A row without a name keeps the key, so a language is never missing"""
        data = {'DashboardBase': {'_info': {'zh-CN': {'help': '看板基础数据'}}}}
        result = json_dumps_custom_indent(ParseStore._names_only(data)).decode('utf-8')
        assert result == """\
{
  "DashboardBase": {
    "_info": {
      "zh-CN": {"name": ""}
    }
  }
}"""

    def test_options_are_dropped(self):
        """The store has no arg with options, an unexpected key is dropped as well"""
        data = {'DashboardBase': {'Value': {'en-US': {'name': 'Value', 'option_i18n': {'a': 'A'}}}}}
        result = json_dumps_custom_indent(ParseStore._names_only(data)).decode('utf-8')
        assert result == """\
{
  "DashboardBase": {
    "Value": {
      "en-US": {"name": "Value"}
    }
  }
}"""
