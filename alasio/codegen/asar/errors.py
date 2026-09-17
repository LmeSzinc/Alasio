"""
Exceptions of the asar packer / unpacker.
"""


class AsarError(Exception):
    """
    Base class of all asar errors.
    """


class AsarFormatError(AsarError):
    """
    The archive is malformed, truncated or breaks the asar format.

    An archive is untrusted input (the updater downloads it), so every
    structural violation is reported with this error instead of being
    silently tolerated.
    """


class AsarEntryNotFoundError(AsarError):
    """
    The requested entry does not exist in the archive.
    """


class AsarPathError(AsarError):
    """
    A path breaks the safety rules.

    Raised when a local file can not be packed (invalid filename), or when an
    archive entry would escape the extraction directory.
    """


class AsarUnsupportedError(AsarError):
    """
    The request is valid but not supported by the asar format.
    """
