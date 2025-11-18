def remove_quote(v):
    """
    Args:
        v (str):

    Returns:
        str:
    """
    if v.startswith('['):
        if v.endswith(']'):
            return v[1:-1]
        else:
            return v[1:]
    else:
        if v.endswith(']'):
            return v[:-1]
        else:
            return v


class Props:
    def __init__(self, props: "dict[str, list[str]]"):
        """
        Helper class to operate `getprop` result
        """
        self.props = props

    def __str__(self):
        return f'<Props count={len(self.props)}>'

    __repr__ = __str__

    @classmethod
    def from_getprop(cls, data: "bytes | str") -> "Props":
        """
        Parse result from `getprop`
        """
        if isinstance(data, bytes):
            data = data.decode('utf-8', errors='replace')

        props = {}
        for row in data.splitlines():
            # [ro.system.build.version.sdk]: [32]
            # [ro.system.product.cpu.abilist]: [x86_64,x86,arm64-v8a,armeabi-v7a,armeabi]
            # [ro.hwui.use_vulkan]: []
            # [debug.tracing.screen_brightness]: [0.05]
            key, sep, value = row.partition(':')
            if not sep:
                continue
            key = remove_quote(key.strip())
            value = remove_quote(value.strip())
            props[key] = value.split(',')

        return cls(props)

    def get(self, key: str) -> "list[str]":
        return self.props.get(key, [])

    def get_str(self, key: str, default: "str | None" = None) -> str:
        values = self.props.get(key)
        if not values:
            return default
        try:
            value = values[0]
        except IndexError:
            # this shouldn't happen
            return default
        return value

    def get_float(self, key: str, default: "float | None" = None) -> float:
        values = self.props.get(key)
        if not values:
            return default
        try:
            value = values[0]
        except IndexError:
            # this shouldn't happen
            return default
        try:
            value = float(value)
        except ValueError:
            return default
        return value

    def get_int(self, key: str, default: "int | None" = None) -> int:
        values = self.props.get(key)
        if not values:
            return default
        try:
            value = values[0]
        except IndexError:
            # this shouldn't happen
            return default
        try:
            value = int(value)
        except ValueError:
            # "0.05" -> 0
            try:
                value = int(float(value))
            except ValueError:
                return default
        return value
