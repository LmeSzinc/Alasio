import pytest

from alasio.adb.protocol.cnxn import DeviceFeatures

pytestmark = [
    pytest.mark.parallel,
]


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
        # a banner without a feature list is a device that has none, its
        # properties are not features
        assert device_features.features == []
        assert device_features.shell_v2 is False

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
        # the property is not a feature of a device that advertises none
        assert device_features.features == []


class TestDeviceFeaturesBanner:
    """Banners of real devices, in every shape adbd sends them"""

    # Captured from a real device (127.0.0.1:16384), the banner of Android 12
    REAL_BANNER = (
        b'device::ro.product.name=PGJM10;ro.product.model=PGJM10;ro.product.device=PGJM10;'
        b'features=shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb,fixed_push_symlink_timestamp,'
        b'abb_exec,remount_shell,track_app,sendrecv_v2,sendrecv_v2_brotli,sendrecv_v2_lz4,'
        b'sendrecv_v2_zstd,sendrecv_v2_dry_run_send,openscreen_mdns'
    )

    def test_real_banner(self):
        """The features of a real device are parsed out of its banner"""
        features = DeviceFeatures.from_cnxn(self.REAL_BANNER)

        assert b'shell_v2' in features.features
        assert b'cmd' in features.features
        assert b'abb' in features.features
        assert b'openscreen_mdns' in features.features
        assert features.shell_v2 is True

    def test_features_with_trailing_semicolon(self):
        """A banner may end the feature list with a semicolon"""
        features = DeviceFeatures.from_cnxn(b'device::features=shell_v2,cmd;')

        assert features.features == [b'shell_v2', b'cmd']

    def test_features_are_not_the_last_key(self):
        """A property after the features does not become part of them"""
        features = DeviceFeatures.from_cnxn(b'device::features=shell_v2,cmd;ro.product.name=x')

        assert features.features == [b'shell_v2', b'cmd']

    def test_legacy_banner_without_shell_v2(self):
        """A device older than Android 7 has no shell_v2 at all"""
        features = DeviceFeatures.from_cnxn(b'device::ro.product.name=legacy;features=cmd,stat_v2,ls_v2')

        assert features.features == [b'cmd', b'stat_v2', b'ls_v2']
        assert features.shell_v2 is False

    def test_banner_of_a_very_old_device(self):
        """The oldest adbd sends a banner without a feature list"""
        features = DeviceFeatures.from_cnxn(b'device::')

        assert features.features == []
        assert features.shell_v2 is False

    def test_banner_without_double_colon(self):
        """A banner that is a bare list is read as a list"""
        features = DeviceFeatures.from_cnxn(b'shell_v2,cmd')

        assert features.features == [b'shell_v2', b'cmd']

    def test_banner_of_android_6(self):
        """The banner of a device before shell_v2 has properties only"""
        banner = (b'device::ro.product.name=MI 5;ro.product.model=MI 5;'
                  b'ro.product.device=gemini;ro.build.version.sdk=23')
        features = DeviceFeatures.from_cnxn(banner)

        assert features.features == []
        assert features.shell_v2 is False

    def test_feature_names_are_case_sensitive(self):
        """The protocol names are lower case, a device that shouts is not one"""
        features = DeviceFeatures.from_cnxn(b'device::features=SHELL_V2,CMD')

        assert features.shell_v2 is False

    def test_last_features_key_wins(self):
        """A banner with the key twice is read the way a parser reads it"""
        features = DeviceFeatures.from_cnxn(b'device::features=cmd;features=shell_v2,abb')

        assert features.features == [b'shell_v2', b'abb']

    def test_feature_with_surrounding_space(self):
        """A space is not a separator of the protocol, it stays in the name"""
        features = DeviceFeatures.from_cnxn(b'device::features=shell_v2, cmd')

        assert features.features == [b'shell_v2', b' cmd']
        assert features.shell_v2 is True


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
