"""
Tests of read_yaml_mapping() and read_json_mapping().

These readers are the generation-input counterparts of the tolerant
read_yaml() / read_msgspec() used by the client: malformed content and a
non-mapping root raise DefinitionError instead of silently disappearing.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError, read_json_mapping, read_yaml_mapping
from alasio.testing.filesystem import fs  # noqa: F401


class TestReadYamlMapping:
    def test_valid_mapping(self, fs):
        fs.create_file('/data.yaml', contents='Group:\n  args:\n    Enable: true\n')
        assert read_yaml_mapping('/data.yaml') == {'Group': {'args': {'Enable': True}}}

    def test_unicode(self, fs):
        fs.create_file('/data.yaml', contents='name: 测试\n')
        assert read_yaml_mapping('/data.yaml') == {'name': '测试'}

    def test_missing_file(self, fs):
        assert read_yaml_mapping('/missing.yaml') == {}

    def test_empty_file(self, fs):
        fs.create_file('/data.yaml', contents='')
        assert read_yaml_mapping('/data.yaml') == {}

    def test_null_document(self, fs):
        fs.create_file('/data.yaml', contents='null\n')
        assert read_yaml_mapping('/data.yaml') == {}

    def test_malformed_yaml(self, fs):
        fs.create_file('/data.yaml', contents='port: [unclosed\n')
        with pytest.raises(DefinitionError) as e:
            read_yaml_mapping('/data.yaml')
        assert e.value.msg.startswith('Invalid yaml: ')
        assert e.value.file == '/data.yaml'

    def test_non_utf8_yaml(self, fs):
        fs.create_file('/data.yaml', contents=b'port: \xff\xfe\n')
        with pytest.raises(DefinitionError) as e:
            read_yaml_mapping('/data.yaml')
        assert e.value.msg.startswith('Invalid yaml: ')
        assert e.value.file == '/data.yaml'

    @pytest.mark.parametrize('contents, kind', [
        ('- 1\n- 2\n', 'list'),
        ('Group\n', 'str'),
        ('42\n', 'int'),
    ], ids=['list', 'str', 'int'])
    def test_root_must_be_mapping(self, fs, contents, kind):
        fs.create_file('/data.yaml', contents=contents)
        with pytest.raises(DefinitionError) as e:
            read_yaml_mapping('/data.yaml')
        assert e.value.msg == f'Expected a mapping at yaml root, got {kind}'
        assert e.value.file == '/data.yaml'


class TestReadJsonMapping:
    def test_valid_mapping(self, fs):
        fs.create_file('/data.json', contents='{"Group": {"args": {"Enable": true}}}')
        assert read_json_mapping('/data.json') == {'Group': {'args': {'Enable': True}}}

    def test_unicode(self, fs):
        fs.create_file('/data.json', contents='{"name": "测试"}')
        assert read_json_mapping('/data.json') == {'name': '测试'}

    def test_missing_file(self, fs):
        assert read_json_mapping('/missing.json') == {}

    def test_empty_file(self, fs):
        fs.create_file('/data.json', contents='')
        assert read_json_mapping('/data.json') == {}

    def test_null_document(self, fs):
        fs.create_file('/data.json', contents='null')
        assert read_json_mapping('/data.json') == {}

    def test_malformed_json(self, fs):
        fs.create_file('/data.json', contents='{"port": [}')
        with pytest.raises(DefinitionError) as e:
            read_json_mapping('/data.json')
        assert e.value.msg.startswith('Invalid json: ')
        assert e.value.file == '/data.json'

    @pytest.mark.parametrize('contents, kind', [
        ('[1, 2]', 'list'),
        ('"Group"', 'str'),
        ('42', 'int'),
    ], ids=['list', 'str', 'int'])
    def test_root_must_be_mapping(self, fs, contents, kind):
        fs.create_file('/data.json', contents=contents)
        with pytest.raises(DefinitionError) as e:
            read_json_mapping('/data.json')
        assert e.value.msg == f'Expected a mapping at json root, got {kind}'
        assert e.value.file == '/data.json'
