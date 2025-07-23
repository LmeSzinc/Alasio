from collections import deque


def yaml_formatter(yaml_bytes):
    """
    Format YAML text according to specified spacing rules using generator approach

    Rules:
    1. Root-level objects are separated by one blank line
    2. Entire text ends with one blank line
    3. Second-level and deeper objects have no blank lines between them
    4. If root-level comments end with blank line, treat as standalone block with trailing blank line
    5. If root-level comments are immediately followed by object, treat comment+object as one unit

    Args:
        yaml_bytes (bytes): YAML text as bytes

    Returns:
        bytes: Formatted YAML text
    """
    lines = yaml_bytes.strip().splitlines()
    print(repr(lines))
    if not lines:
        return b''

    formatted_lines = _yaml_line_generator(lines)
    return b'\n'.join(formatted_lines)


def _yaml_line_generator(lines):
    """
    Generator that yields formatted YAML lines with proper spacing

    Args:
        lines (list[bytes]): List of YAML lines as bytes

    Yields:
        bytes: Formatted lines
    """
    cache = deque()
    is_grouped_comment = False

    for line in lines:
        line = line.rstrip()

        # Guard: Handle empty lines
        if not line:
            if is_grouped_comment:
                # Empty line after comment block - treat as standalone comment block
                yield from cache
                yield b''
                cache.clear()
                is_grouped_comment = False
            continue

        # Guard: Handle nested content (indented lines)
        if line.startswith((b' ', b'\t')):
            is_grouped_comment = False
            cache.append(line)
            continue

        # Handle root-level content (no indentation)

        # Guard: Handle root-level comments
        if line.startswith(b'#'):
            if cache and not is_grouped_comment:
                # Yield previous non-comment cache with spacing
                yield from cache
                yield b''
                cache.clear()

            cache.append(line)
            is_grouped_comment = True
            continue

        # Handle regular root-level objects

        # Guard: Check if we have grouped comment
        if is_grouped_comment:
            # We have comments in cache, add this object to the same cache (grouped)
            cache.append(line)
            is_grouped_comment = False
            continue
        # No grouped comment - yield previous cache if exists and start fresh
        if cache:
            yield from cache
            yield b''
            cache.clear()
        cache.append(line)

    # Handle final cached content and ensure file ends with blank line
    if cache:
        yield from cache
    yield b''
