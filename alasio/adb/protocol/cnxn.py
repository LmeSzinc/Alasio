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
            # a banner is the type of the peer, then its properties. A device of
            # an old android has no feature list at all, and its properties are
            # not features.
            _, sep, payload = data.partition(b'::')
        else:
            sep = b''
            payload = data

        # the last features key wins, a banner may not be the last of them
        value = None
        for kv in payload.split(b';'):
            key, eq, part = kv.partition(b'=')
            if eq and key == b'features':
                value = part
        if value is not None:
            return cls([f for f in value.split(b',') if f])
        if sep:
            # a banner of a device that advertises no feature at all
            return cls([])

        # without a banner the payload is the feature list itself
        return cls([f for f in payload.split(b',') if f])

    @cached_property
    def shell_v2(self):
        return b'shell_v2' in self.features
