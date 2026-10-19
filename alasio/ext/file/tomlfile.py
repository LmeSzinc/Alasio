def get_tomllib():
    """
    Get the TOML parser module.

    tomllib is the standard library module of Python 3.11+, tomli is the
    backport with the same API for the older versions. The import is
    deferred, so the module can be imported where TOML is never parsed
    without requiring tomli on Python < 3.11.

    Returns:
        module: tomllib on Python 3.11+, tomli on the older versions

    Raises:
        ImportError: If tomllib is not available and tomli is not installed
    """
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            raise ImportError('Dependency `tomli` is required on Python < 3.11') from None
    return tomllib


def toml_loads(data):
    """
    Parse a TOML document.

    Args:
        data (bytes | str): Content of the document, bytes are decoded as
            UTF-8 as the TOML specification requires

    Returns:
        dict: Parsed document

    Raises:
        ImportError: If tomllib is not available and tomli is not installed
        ValueError: If the content is not a TOML document, TOMLDecodeError is
            a ValueError
    """
    if isinstance(data, (bytes, bytearray)):
        data = data.decode('utf-8')
    return get_tomllib().loads(data)
