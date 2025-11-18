import pytest
from alasio.adb.protocol.cnxn import DeviceFeatures


class TestDeviceFeatures:
    """Test DeviceFeatures class"""

    def test_init(self):
        """Test initialization method"""
        features = [b'shell_v2', b'cmd', b'stat_v2']
        device_features = DeviceFeatures(features)
        assert device_features.features == features

    def test_str_representation(self):
        """Test string representation"""
        features = [b'shell_v2', b'cmd']
        device_features = DeviceFeatures(features)
        expected = f"DeviceFeatures([b'shell_v2', b'cmd'])"
        assert str(device_features) == expected
        assert repr(device_features) == expected

    def test_from_cnxn_full_response(self):
        """Test parsing from full CNXN response"""
        data = b"device::ro.product.name=PGJM10;ro.product.model=PGJM10;ro.product.device=PGJM10;features=shell_v2,cmd"
        device_features = DeviceFeatures.from_cnxn(data)
        assert device_features.features == [b'shell_v2', b'cmd']

    def test_from_cnxn_simple_list(self):
        """Test parsing from simple feature list"""
        data = b"shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb"
        device_features = DeviceFeatures.from_cnxn(data)
        expected_features = [
            b'shell_v2', b'cmd', b'stat_v2', b'ls_v2',
            b'fixed_push_mkdir', b'apex', b'abb'
        ]
        assert device_features.features == expected_features

    def test_from_cnxn_with_multiple_key_value_pairs(self):
        """Test parsing with multiple key-value pairs"""
        data = b"device::ro.product.name=Test;features=shell_v2,cmd,stat_v2;ro.version=10"
        device_features = DeviceFeatures.from_cnxn(data)
        assert device_features.features == [b'shell_v2', b'cmd', b'stat_v2']

    def test_from_cnxn_empty_features(self):
        """Test parsing empty feature list"""
        data = b"features="
        device_features = DeviceFeatures.from_cnxn(data)
        assert device_features.features == []

    def test_from_cnxn_no_features_key(self):
        """Test parsing when features key is not present"""
        data = b"device::ro.product.name=Test;ro.product.model=Test"
        device_features = DeviceFeatures.from_cnxn(data)
        # Should parse the entire data as feature list
        assert len(device_features.features) > 0

    def test_from_cnxn_with_empty_values(self):
        """Test parsing feature list with empty values"""
        data = b"shell_v2,,cmd,,stat_v2"
        device_features = DeviceFeatures.from_cnxn(data)
        # Empty values should be filtered out
        assert device_features.features == [b'shell_v2', b'cmd', b'stat_v2']

    def test_shell_v2_present(self):
        """Test shell_v2 feature is present"""
        features = [b'shell_v2', b'cmd', b'stat_v2']
        device_features = DeviceFeatures(features)
        assert device_features.shell_v2 is True

    def test_shell_v2_absent(self):
        """Test shell_v2 feature is absent"""
        features = [b'cmd', b'stat_v2', b'ls_v2']
        device_features = DeviceFeatures(features)
        assert device_features.shell_v2 is False

    def test_shell_v2_cached_property(self):
        """Test shell_v2 is a cached property"""
        features = [b'shell_v2', b'cmd']
        device_features = DeviceFeatures(features)

        # First access
        result1 = device_features.shell_v2
        # Second access should return cached value
        result2 = device_features.shell_v2

        assert result1 is result2
        assert result1 is True

    def test_from_cnxn_single_feature(self):
        """Test parsing single feature"""
        data = b"shell_v2"
        device_features = DeviceFeatures.from_cnxn(data)
        assert device_features.features == [b'shell_v2']

    def test_from_cnxn_with_double_colon_but_no_features_key(self):
        """Test parsing with double colon but no features key"""
        data = b"device::ro.product.name=Test"
        device_features = DeviceFeatures.from_cnxn(data)
        # Should treat ro.product.name=Test as a feature
        assert len(device_features.features) == 1


# Parametrized tests
class TestDeviceFeaturesParametrized:
    """Parametrized tests for multiple scenarios"""

    @pytest.mark.parametrize("data,expected_features", [
        (
                b"device::features=shell_v2,cmd",
                [b'shell_v2', b'cmd']
        ),
        (
                b"shell_v2,cmd,stat_v2",
                [b'shell_v2', b'cmd', b'stat_v2']
        ),
        (
                b"device::a=b;features=apex,abb;c=d",
                [b'apex', b'abb']
        ),
        (
                b"",
                []
        ),
        (
                b"single_feature",
                [b'single_feature']
        ),
    ])
    def test_from_cnxn_parametrized(self, data, expected_features):
        """Parametrized test for from_cnxn method"""
        device_features = DeviceFeatures.from_cnxn(data)
        assert device_features.features == expected_features

    @pytest.mark.parametrize("features,has_shell_v2", [
        ([b'shell_v2', b'cmd'], True),
        ([b'cmd', b'stat_v2'], False),
        ([b'shell_v2'], True),
        ([], False),
        ([b'shell_v1', b'cmd'], False),
    ])
    def test_shell_v2_parametrized(self, features, has_shell_v2):
        """Parametrized test for shell_v2 property"""
        device_features = DeviceFeatures(features)
        assert device_features.shell_v2 is has_shell_v2


# Edge case tests
class TestDeviceFeaturesEdgeCases:
    """Test edge cases"""

    def test_features_with_special_characters(self):
        """Test features with special characters"""
        data = b"shell_v2,cmd-test,stat.v2"
        device_features = DeviceFeatures.from_cnxn(data)
        assert device_features.features == [b'shell_v2', b'cmd-test', b'stat.v2']

    def test_very_long_feature_list(self):
        """Test very long feature list"""
        features = [f'feature_{i}'.encode() for i in range(100)]
        device_features = DeviceFeatures(features)
        assert len(device_features.features) == 100

    def test_duplicate_features(self):
        """Test duplicate features"""
        data = b"shell_v2,cmd,shell_v2,cmd"
        device_features = DeviceFeatures.from_cnxn(data)
        # Should not deduplicate, keep as is
        assert device_features.features == [b'shell_v2', b'cmd', b'shell_v2', b'cmd']

    def test_features_only_commas(self):
        """Test input with only commas"""
        data = b",,,"
        device_features = DeviceFeatures.from_cnxn(data)
        assert device_features.features == []
