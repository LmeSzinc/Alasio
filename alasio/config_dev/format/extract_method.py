import ast


def extract_method(source):
    """
    Extract class methods from source code using ast.

    Walk the AST to find all class definitions, then extract each method's
    source lines by cropping the original source by line number range.
    The extracted source includes method decorators if present.

    Args:
        source (str): Python source code

    Returns:
        dict: Nested dict with (class_name, method_name) keys,
              values are list of source code lines preserving exact content.
              e.g. {"ClassName": {"method_name": ["def method(self):", "    pass"]}}
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    result = {}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        class_name = node.name
        methods = {}
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            method_name = item.name
            # Start from the first decorator if the method has any
            if item.decorator_list:
                start_line = item.decorator_list[0].lineno
            else:
                start_line = item.lineno
            end_line = item.end_lineno
            # lineno is 1-based, list index is 0-based
            methods[method_name] = lines[start_line - 1:end_line]
        if methods:
            result[class_name] = methods

    return result
