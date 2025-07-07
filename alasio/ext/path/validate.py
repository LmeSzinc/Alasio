from .calc import get_stem


def validate_filename(filename):
    """
    Validates a filename against the strictest set of rules from all major
    operating systems and file systems, optimized for performance and clarity.

    The checks are ordered from cheapest/most-likely-to-fail to most-expensive.

    If the filename is valid, the function returns silently.
    If the filename is invalid, it raises a ValueError with a specific reason.

    Args:
        filename (str): The filename to validate.

    Raises:
        ValueError: If the filename violates any of the universal safety rules.
    """
    # --- Check 1: Basic Sanity (Fastest) ---
    if not isinstance(filename, str):
        raise ValueError('Filename should be a string')
    if not filename:
        raise ValueError('Filename cannot be empty')

    # --- Check 2: Preliminary Length Check ---
    if len(filename) > 255:
        raise ValueError(f'Filename is too long, length should <= 255')

    # --- Check 3: Character-by-Character Validation ---
    for char in '\\/:*?"<>|':
        if char in filename:
            raise ValueError(f'Filename should not contain character: "{char}"')
    for char in filename:
        char_ord = ord(char)
        if char_ord < 32 or char_ord == 127:
            raise ValueError(f'Filename should not contain control character (ASCII: {char_ord})')

    # --- Check 4: Reserved Names ---
    # Check for exact matches against names like `$MFT` or `.`
    if filename == '.' or filename == '..':
        raise ValueError(f'Filename cannot be directory pointer: "{filename}"')
    upper = filename.upper()
    if upper in [
        "$MFT", "$MFTMIRR", "$LOGFILE", "$VOLUME", "$ATTRDEF", "$BITMAP",
        "$BOOT", "$BADCLUS", "$SECURE", "$UPCASE", "$EXTEND",
    ]:
        raise ValueError(f'Filename cannot be NTFS metadata name: {upper}')

    # Check for basenames like `CON` or `LPT1`
    base_name = get_stem(upper)
    if base_name in [
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ]:
        raise ValueError(f'Filename cannot be reserved system name: {base_name}')

    # --- Check 5: Ending Character Restriction ---
    if filename.startswith(' '):
        raise ValueError('Filename cannot start with a <space>')
    if filename.endswith(' '):
        raise ValueError('Filename cannot end with a <space>')
    if filename.endswith('.'):
        raise ValueError('Filename cannot end with a <dot>')

    # --- Check 6: Final Byte-Length Check (Most Expensive) ---
    # This is the definitive check for multi-byte character strings (e.g., UTF-8).
    # It's placed last because the encode() operation is more costly than other checks.
    try:
        byte_length = len(filename.encode('utf-8'))
    except UnicodeEncodeError as e:
        # This case is rare but possible with malformed strings.
        raise ValueError(f'Filename contains invalid characters that cannot be UTF-8 encoded: {e}')

    if byte_length > 255:
        raise ValueError(f'Filename is too long, byte_length should <= 255')

    # If all checks pass, the function returns silently (returning None).
