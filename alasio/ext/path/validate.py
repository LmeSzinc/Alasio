import os

from .calc import get_rootstem


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

    # --- Check 4: Ending Character Restriction ---
    if filename.startswith(' '):
        raise ValueError('Filename cannot start with a <space>')
    if filename.endswith(' '):
        raise ValueError('Filename cannot end with a <space>')
    if filename.endswith('.'):
        raise ValueError('Filename cannot end with a <dot>')

    # --- Check 5: Reserved Names ---
    # Check for exact matches against names like `$MFT` or `.`
    if filename == '.' or filename == '..':
        raise ValueError(f'Filename cannot be directory pointer: "{filename}"')
    upper = filename.upper()
    if upper in (
            "$MFT", "$MFTMIRR", "$LOGFILE", "$VOLUME", "$ATTRDEF", "$BITMAP",
            "$BOOT", "$BADCLUS", "$SECURE", "$UPCASE", "$EXTEND",
    ):
        raise ValueError(f'Filename cannot be NTFS metadata name: {upper}')

    # Check for basenames like `CON` or `LPT1`
    base_name = get_rootstem(upper).lstrip(' ').rstrip('. ')
    if base_name in (
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ):
        raise ValueError(f'Filename cannot be reserved system name: {base_name}')

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


def _validate_path_component(component: str):
    """
    Validates a single path component (a filename or directory name) against
    the strictest cross-platform rules. Raises ValueError on failure.

    This is an internal helper function designed to mirror the style of the
    provided `validate_filename` example.
    """
    # --- Check 1: Basic Sanity ---
    # The calling function ensures the component is a string.
    if not component:
        raise ValueError('Path component cannot be empty')

    # --- Check 2: Length Check ---
    if len(component) > 255:
        raise ValueError(f'Path component is too long, length should <= 255')

    # --- Check 3: Illegal Character Validation ---
    for char in '\\/:*?"<>|':
        if char in component:
            raise ValueError(f'Path component should not contain character: "{char}"')
    for char in component:
        char_ord = ord(char)
        if char_ord < 32 or char_ord == 127:  # Control chars & DEL
            raise ValueError(f'Path component should not contain control character (ASCII: {char_ord})')

    # --- Check 4: Start/End Character Restriction ---
    if component.startswith(' '):
        raise ValueError('Path component cannot start with a <space>')
    if component.endswith(' '):
        raise ValueError('Path component cannot end with a <space>')
    if component.endswith('.'):
        raise ValueError('Path component cannot end with a <dot>')

    # --- Check 5: Reserved Names ---
    if component in ('.', '..'):
        raise ValueError(f'Path component cannot be directory pointer: "{component}"')

    upper = component.upper()
    if upper in (
            "$MFT", "$MFTMIRR", "$LOGFILE", "$VOLUME", "$ATTRDEF", "$BITMAP",
            "$BOOT", "$BADCLUS", "$SECURE", "$UPCASE", "$EXTEND",
    ):
        raise ValueError(f'Path component cannot be NTFS metadata name: {upper}')

    # Check for basenames like `CON` or `LPT1`
    base_name = get_rootstem(upper).lstrip(' ').rstrip('. ')
    if base_name in (
            "CON", "PRN", "AUX", "NUL",
            "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
            "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ):
        raise ValueError(f'Path component cannot be reserved system name: {base_name}')

    # --- Check 6: Final Byte-Length Check (Most Expensive) ---
    try:
        byte_length = len(component.encode('utf-8'))
    except UnicodeEncodeError as e:
        raise ValueError(f'Path component contains invalid characters that cannot be UTF-8 encoded: {e}')

    if byte_length > 255:
        raise ValueError(f'Path component is too long, byte_length should <= 255')


def validate_filepath(path: str):
    """
    Validates a relative path by checking each of its components (directories
    and the final filename) against the strictest cross-platform rules.

    It ensures the path is relative and structurally sound before delegating
    component-level checks to a helper function.

    If the path is valid, the function returns silently.
    If the path is invalid, it raises a ValueError with a specific reason.

    Args:
        path (str): The relative path to validate.

    Raises:
        ValueError: If the path violates any of the safety rules.
    """
    # --- Check 1: Basic Sanity on the whole path ---
    if not isinstance(path, str):
        raise ValueError('Path should be a string')
    if not path:
        raise ValueError('Path cannot be empty')

    # --- Check 2: Overall Structure ---
    if os.path.isabs(path) or path.startswith(('\\', '/')):
        raise ValueError('Path cannot be absolute or start with a slash')

    # A safe, cross-platform limit for the entire path.
    if len(path) > 4096:
        raise ValueError('Path is too long, length should <= 4096')

    # --- Check 3: Decompose and Validate Each Component ---
    # Normalize separators to '/' for consistent splitting.
    normalized_path = path.replace('\\', '/')

    # Split the path into its components. A trailing slash is not allowed
    # as it would produce an empty component at the end of the list.
    components = normalized_path.split('/')

    for component in components:
        _validate_path_component(component)

    # If all checks pass, the function returns silently.


def validate_resolve_path(root: str, path: str) -> str:
    """
    The complete, two-stage security function to validate and resolve a user-provided
    relative path, ensuring it points to a safe location.

    Stage 1: Input Sanitization
        Validates the user-provided path string itself against the strictest
        cross-platform rules for characters, names, and structure.

    Stage 2: Boundary Checking
        Resolves the sanitized path against the filesystem (including handling '..'
        and symbolic links) and ensures the final, real path is strictly
        within the safe `base_directory`.

    If the path is valid and safe, returns the absolute, canonical path.
    If any check fails, raises a ValueError.

    Args:
        root (str): The absolute path to the safe "jail" directory.
        path (str): The untrusted relative path provided by the user.

    Returns:
        str: The safe, absolute, real path on the filesystem.

    Raises:
        ValueError: If the path is invalid, unsafe, or any configuration is wrong.
    """
    # --- Stage 1: Input Sanitization ---
    # First, validate the user's input string before it ever touches the filesystem API.
    # This prevents creating malformed or dangerous filenames.
    validate_filepath(path)

    # --- Stage 2: Boundary Checking ---
    # The input string is clean. Now, check where it *actually* points.

    # Ensure the base directory is a valid, absolute path to a directory.
    # This is a server configuration check.
    root = os.path.abspath(root)

    # Combine the safe base with the sanitized user path.
    combined_path = os.path.join(root, path)

    # Resolve the path to its canonical form, eliminating '..' and following symlinks.
    # This is the most critical step for preventing path traversal.
    combined_path = os.path.realpath(combined_path)

    # The final check: ensure the resolved path is still inside our safe directory.
    common_path = os.path.commonpath([root, combined_path])
    if common_path != root:
        raise ValueError('Path traversal detected: The path resolves to a location outside the allowed directory.')

    # If all checks pass, return the safe, absolute, canonical path.
    return combined_path
