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
    def __init__(self, props: "dict[str, str]"):
        """
        Helper class to operate `getprop` result

        Args:
            props: Property name to its raw value. A value may contain commas,
                they are only split by get()
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
            # the raw value is kept, a comma inside a value is not a separator
            # of a list: "zh-CN,en-US" is one value of a locale, while a list
            # like ro.product.cpu.abilist is only a list by convention
            props[key] = remove_quote(value.strip())

        return cls(props)

    def get(self, key: str) -> "list[str]":
        """
        Get a property as a list of comma separated values

        Args:
            key (str): Property name

        Returns:
            list: The values, an empty list when the property is not set
        """
        try:
            value = self.props[key]
        except KeyError:
            return []
        return value.split(',')

    def get_str(self, key: str, default: "str | None" = None) -> str:
        """
        Get the raw value of a property

        Args:
            key (str): Property name
            default (str): Value to return when the property is not set

        Returns:
            str: The value as `getprop` prints it, commas included
        """
        return self.props.get(key, default)

    def get_float(self, key: str, default: "float | None" = None) -> float:
        """
        Get a property as a float

        Args:
            key (str): Property name
            default (float): Value to return when the property is not set or
                is not a number

        Returns:
            float: The value
        """
        value = self.props.get(key)
        if not value:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def get_int(self, key: str, default: "int | None" = None) -> int:
        """
        Get a property as an integer

        Args:
            key (str): Property name
            default (int): Value to return when the property is not set or is
                not a number

        Returns:
            int: The value, a float string is truncated
        """
        value = self.props.get(key)
        if not value:
            return default
        try:
            return int(value)
        except ValueError:
            # "0.05" -> 0
            try:
                return int(float(value))
            except ValueError:
                return default
