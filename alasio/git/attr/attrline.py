from collections import deque


def parse_gitattributes_line(line):
    """
    Args:
        line (str):
            *.py     text diff=python
            package-lock.json text eol=lf -diff

    Returns:
        tuple[str, dict[str, str]]: (pathspec, attrs_dict)
            attrs_dict may contain:
                text: "set" for text, "unset" for binary, "auto" to depends on content
                eol: "lf" or "crlf"
                diff: diff driver or "" for unset
                merge: merge operation or "" for unset
    """
    line = line.strip()
    if not line or line.startswith('#'):
        return '', {}

    if line.startswith('"'):
        if '\\' in line:
            # escape inside quote
            # example: "my \"quoted\" file.txt" merge=ours
            # Only handle \\space and \\" escapes, keep others as-is
            escape = False
            end = 0
            seen_opening = False
            pathspec_char = deque()
            for char in line:
                end += 1
                if escape:
                    if char == '"' or char == ' ':
                        # consume backslash, keep the quoted/escaped char
                        pathspec_char.append(char)
                    else:
                        # keep escape sequence as-is
                        pathspec_char.append('\\')
                        pathspec_char.append(char)
                    escape = False
                elif char == '\\':
                    escape = True
                elif char == '"':
                    if not seen_opening:
                        seen_opening = True
                    else:
                        # end at closing quote
                        break
                else:
                    pathspec_char.append(char)
            if end:
                pathspec = ''.join(pathspec_char)
                rest = line[end:].strip()
                if not rest:
                    # just bare pathspec, no attrs
                    return pathspec, {}
            else:
                # just bare pathspec, no attrs
                return ''.join(pathspec_char), {}
        else:
            # quoted but no escape
            # example: "file name.txt" text eol=lf
            pathspec, sep, rest = line[1:].partition('"')
            if not sep:
                # quote is unpaired, keep leading quote in pathspec
                # example: "filename.txt text eol=lf
                pathspec, sep, rest = line.partition(' ')
            rest = rest.strip()
            if not rest:
                # just bare pathspec, no attrs
                return pathspec, {}
    else:
        if '\\' in line:
            # escape but no quote
            # example: file\ name.txt    text eol=lf
            # Only handle \\space and \\" escapes, keep others as-is
            escape = False
            end = 0
            pathspec_char = deque()
            for char in line:
                end += 1
                if escape:
                    if char == ' ':
                        # \\space -> space (consume backslash, don't break)
                        pathspec_char.append(' ')
                    elif char == '"':
                        # \\" -> " (consume backslash)
                        pathspec_char.append('"')
                    elif char.isspace():
                        # e.g. \\\t, \\\n before actual separator
                        pathspec_char.append('\\')
                        pathspec_char.append(char)
                    else:
                        # keep escape sequence as-is
                        pathspec_char.append('\\')
                        pathspec_char.append(char)
                    escape = False
                elif char == '\\':
                    escape = True
                elif char.isspace():
                    # end at space
                    break
                else:
                    pathspec_char.append(char)
            if end:
                pathspec = ''.join(pathspec_char)
                rest = line[end:].strip()
                if not rest:
                    # just bare pathspec, no attrs
                    return pathspec, {}
            else:
                # just bare pathspec, no attrs
                return ''.join(pathspec_char), {}
        else:
            # no escape no quote
            # example: *.py     text diff=python
            pathspec, _, rest = line.partition(' ')
            rest = rest.strip()
            if not rest:
                # just bare pathspec, no attrs
                return pathspec, {}

    # parse attrs
    attrs = {}
    for token in rest.split():
        if not token:
            continue
        if token == 'text':
            attrs['text'] = 'set'
        elif token == 'binary':
            # binary is equivalent to -text -diff -merge
            attrs['text'] = 'unset'
            attrs['diff'] = ''
            attrs['merge'] = ''
        elif token == '-text':
            attrs['text'] = 'unset'
        elif token == '-diff':
            attrs['diff'] = ''
        elif token == '-merge':
            attrs['merge'] = ''
        else:
            # key=value
            key, sep, value = token.partition('=')
            if not sep:
                continue
            if key == 'eol':
                if value == 'lf' or value == 'crlf':
                    attrs['eol'] = value
                else:
                    # eol can only be lf/crlf
                    pass
            if key == 'text':
                if value == 'auto':
                    attrs['text'] = 'auto'
                else:
                    # text=value where value is not auto
                    attrs['text'] = 'set'
            elif key == 'diff':
                attrs[key] = value
            elif key == 'merge':
                attrs[key] = value
            # ignore others

    return pathspec, attrs
