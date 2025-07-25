class ParseBase:
    def __init__(self, file):
        """
        Args:
            file (PathStr): Absolute filepath to {nav}.args.yaml
        """
        # {nav}.args.yaml
        self.file = file
        # {nav}.tasks.yaml
        self.tasks_file = file.with_multisuffix('.tasks.yaml')
        # {nav}.config.json
        self.config_file = file.with_name(f'{file.rootstem}_config.json')
        # {nav}_model.py
        # Use "_" in file name because python can't import filename with "." easily
        self.model_file = file.with_name(f'{file.rootstem}_model.py')


_EMPTY = object()


class DefinitionError(Exception):
    def __init__(self, msg, file='', keys='', value=_EMPTY):
        """
        Args:
            msg (str | Exception): Error message
            file (str):
            keys (list[str] | str):
            value (Any):
        """
        if isinstance(msg, Exception):
            msg = f'{msg.__class__.__name__}({msg})'
        self.msg = msg
        self.file = file
        self.keys = keys
        self.value = value

    def __str__(self):
        """
        Show error as:

        {self.__class__.__name__},
            error={self.msg}
            file={self.file}
            keys={self.keys}
            value={self.value}
        """
        parts = [self.__class__.__name__]

        has_location_info = False

        # Add any location information that's available
        if self.file:
            parts.append(f'file={self.file}')
            has_location_info = True

        if self.keys:
            parts.append(f'keys={self.keys}')
            has_location_info = True

        if self.value is not _EMPTY:
            parts.append(f'value={self.value}')
            has_location_info = True

        # Add a separator only if we have location info
        if has_location_info:
            parts.append(f'error={self.msg}')
            return ',\n    '.join(parts)
        else:
            return f'{self.__class__.__name__},\n    error={self.msg}'


def iscapitalized(s):
    """
    Check if a string is capitalized

    Args:
        s (str):

    Returns:
        bool:
    """
    if s:
        return s[0].isupper()
    else:
        return False
