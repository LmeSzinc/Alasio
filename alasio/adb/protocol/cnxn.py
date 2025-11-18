from alasio.ext.cache import cached_property


class DeviceFeatures:
    def __init__(self, features: "list[bytes]"):
        """
        Args:
            features: List of CNXN features
        """
        self.features = features

    def __str__(self):
        return f'{self.__class__.__name__}({self.features})'

    __repr__ = __str__

    @classmethod
    def from_cnxn(cls, data: bytes) -> "DeviceFeatures":
        """
        Args:
            data: CNXN response like
            b"device::ro.product.name=PGJM10;ro.product.model=PGJM10;ro.product.device=PGJM10;features=shell_v2,cmd"
            or just b"shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb"
        """
        if b'::' in data:
            _, _, data = data.partition(b'::')
        for kv in data.split(b';'):
            key, sep, value = kv.partition(b'=')
            if sep and key == b'features':
                data = value
        features = [f for f in data.split(b',') if f]
        return cls(features)

    @cached_property
    def shell_v2(self):
        return b'shell_v2' in self.features
