import ast
from typing import Iterator, List, Optional, Tuple, Union

from msgspec import Struct

from alasio.assets.template import Template
from alasio.base.op import Area, RGB


class MetaTemplate(Struct, omit_defaults=True):
    """Template with metadata"""
    area: Area
    color: RGB

    lang: str = ''
    frame: int = 1
    search: Optional[Area] = None
    button: Optional[Area] = None
    match: Optional[str] = None
    similarity: Optional[float] = None
    colordiff: Optional[int] = None
    file: str = ''

    # meta attributes
    source: Optional[str] = None
    # whether resource file exists, default to True and will be set in AssetFolder
    source_exist: bool = True

    def __repr__(self):
        return f'MetaTemplate(file="{self.file}", area={self.area}, color={self.color})'

    def __post_init__(self):
        # Convert to Area objects
        self.area = Area(self.area)
        self.color = RGB(self.color)
        if self.search:
            self.search = Area(self.search)
        if self.button:
            self.button = Area(self.button)

    def set_file(self, path, name):
        """
        Args:
            path:
            name:
        """
        self.file = Template.construct_filename(path, name, lang=self.lang, frame=self.frame)


class MetaAsset(Struct, omit_defaults=True):
    """Asset with metadata"""

    path: str
    name: str

    search: Optional[Area] = None
    button: Optional[Area] = None
    match: Optional[str] = None
    similarity: Optional[float] = None
    colordiff: Optional[int] = None
    interval: Optional[Union[int, float]] = None

    # meta attributes
    # Asset-level docstring
    doc: str = ''
    # additional resource images for reference
    ref: Tuple[str] = ()
    templates: Tuple[MetaTemplate] = ()

    def __repr__(self):
        return f'MetaAsset(name="{self.name}")'

    def __post_init__(self):
        # Convert to Area objects
        if self.search:
            self.search = Area(self.search)
        if self.button:
            self.button = Area(self.button)


class AssetParser:
    """Build Asset and Template objects directly from AST"""

    def __init__(self, source_code: str):
        self.source_code = source_code
        self.lines = source_code.split('\n')
        self.tree = ast.parse(source_code)

    def iter_line(
            self, start: Optional[int] = None, end: Optional[int] = None, step: Optional[int] = None
    ) -> Iterator[str]:
        """
        Iterate over lines in a range, similar to list slice, with automatic strip

        Args:
            start: Start line number (1-based, None means first line)
            end: End line number (inclusive, None means last line)
            step: Step size (default 1, None also means 1)

        Yields:
            str: Line content with whitespace stripped
        """
        # Handle defaults
        if start is None:
            start = 1
        if end is None:
            end = len(self.lines)
        if step is None:
            step = 1

        # Convert to 0-based index, end is inclusive
        start_idx = start - 1
        end_idx = end

        try:
            for i in range(start_idx, end_idx, step):
                yield self.lines[i].strip()
        except IndexError:
            return

    def find_preceding_comments(self, lineno: int) -> str:
        """
        Find consecutive comment lines starting from the line before the given line

        Returns:
            str: Multi-line comment text separated by \n, with leading/trailing empty lines stripped
        """
        comments = []

        for line in self.iter_line(lineno - 1, 0, -1):
            if not line:
                continue

            if line.startswith('#'):
                comment = line[1:].strip()
                comments.insert(0, comment)
            else:
                break

        # Join into multi-line text and strip leading/trailing empty lines
        text = '\n'.join(comments)
        return text.strip()

    def find_commented_attributes_in_range(
            self,
            start_line: int,
            end_line: int,
            attribute_names: "str | List[str]"
    ) -> dict:
        """
        Find commented attributes within a line range
        Supports both # attr_name= and #attr_name= forms

        Args:
            start_line: Start line number
            end_line: End line number
            attribute_names: Attribute name or list of attribute names
        """
        # Convert to set for O(1) lookup performance
        if isinstance(attribute_names, str):
            attr_set = {attribute_names}
            attributes = {attribute_names: []}
        else:
            attr_set = set(attribute_names)
            attributes = {name: [] for name in attribute_names}

        for line in self.iter_line(start_line, end_line):
            # Check if starts with #
            if not line.startswith('#'):
                continue

            # Remove leading # before splitting
            attr, _, value = line[1:].partition('=')
            if not _:  # No = sign
                continue

            # Strip whitespace
            attr = attr.strip()

            # Check if in set (O(1) lookup)
            if attr in attr_set:
                # Strip whitespace and quotes
                value = value.strip()
                # Remove surrounding quotes (single or double)
                if value and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                attributes[attr].append(value)

        return attributes

    def extract_value_from_node(self, node: ast.AST):
        """Extract Python value from AST node"""
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.Num):
            return node.n
        elif isinstance(node, ast.Str):
            return node.s
        elif isinstance(node, ast.Tuple):
            return tuple(self.extract_value_from_node(e) for e in node.elts)
        elif isinstance(node, ast.List):
            return [self.extract_value_from_node(e) for e in node.elts]
        elif isinstance(node, ast.Name):
            # Return variable name, don't evaluate
            return node.id
        elif isinstance(node, ast.Attribute):
            # Return string representation of attribute access
            obj = self.extract_value_from_node(node.value)
            return f"{obj}.{node.attr}"
        return None

    def build_template_from_call(self, template_call: ast.Call) -> MetaTemplate:
        """
        Build MetaTemplate object from Template() call node

        Raises:
            TypeError: If required parameters (area, color) are missing
        """
        kwargs = {}

        # Extract keyword arguments
        for keyword in template_call.keywords:
            if keyword.arg:
                attr_name = keyword.arg
                attr_value = self.extract_value_from_node(keyword.value)
                kwargs[attr_name] = attr_value

        # Build MetaTemplate, will automatically raise TypeError if area or color is missing
        start_line = template_call.lineno
        end_line = template_call.end_lineno

        # Find commented source attribute within Template's bracket range, take only first one
        commented_attrs = self.find_commented_attributes_in_range(
            start_line, end_line, 'source'
        )
        if commented_attrs['source']:
            kwargs['source'] = commented_attrs['source'][0]

        try:
            template = MetaTemplate(**kwargs)
        except TypeError as e:
            raise TypeError(
                f"Template at line {start_line}: {e}"
            ) from e

        return template

    def build_templates(self, template_node: ast.AST) -> List[MetaTemplate]:
        """
        Build Template list from template parameter
        Supports both lambda and direct tuple forms

        Args:
            template_node: Can be ast.Lambda or ast.Tuple
        """
        templates = []

        # Handle lambda: lambda: (...)
        if isinstance(template_node, ast.Lambda):
            if isinstance(template_node.body, ast.Tuple):
                template_calls = template_node.body.elts
            else:
                return templates
        # Handle direct tuple: (...)
        elif isinstance(template_node, ast.Tuple):
            template_calls = template_node.elts
        else:
            return templates

        # Build templates from call nodes
        for elt in template_calls:
            if isinstance(elt, ast.Call):
                if isinstance(elt.func, ast.Name) and elt.func.id == 'Template':
                    template = self.build_template_from_call(elt)
                    templates.append(template)

        return templates

    def parse_assets(self) -> List[MetaAsset]:
        """
        Parse all Asset assignments, return list of MetaAsset objects

        Raises:
            TypeError: If Asset is missing required parameters (path, name)
        """
        assets = []

        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue

            if not isinstance(node.value, ast.Call):
                continue

            if not (isinstance(node.value.func, ast.Name) and
                    node.value.func.id == 'Asset'):
                continue

            # Collect Asset parameters
            kwargs = {}
            meta_templates = []

            # Get variable name
            asset_name = None
            if node.targets and isinstance(node.targets[0], ast.Name):
                asset_name = node.targets[0].id

            # Record line number range
            start_line = node.lineno
            # end_line = node.end_lineno

            # Extract keyword arguments
            for keyword in node.value.keywords:
                if not keyword.arg:
                    continue

                attr_name = keyword.arg

                # Special handling for template parameter (lambda function)
                if attr_name == 'template':
                    meta_templates = self.build_templates(keyword.value)
                else:
                    attr_value = self.extract_value_from_node(keyword.value)
                    kwargs[attr_name] = attr_value

            # Check required parameters
            if 'path' not in kwargs:
                raise TypeError(
                    f"Asset '{asset_name}' at line {start_line}: missing required argument 'path'"
                )
            if 'name' not in kwargs:
                raise TypeError(
                    f"Asset '{asset_name}' at line {start_line}: missing required argument 'name'"
                )

            # Set metadata
            kwargs['name'] = asset_name
            kwargs['doc'] = self.find_preceding_comments(node.lineno)
            kwargs['templates'] = tuple(meta_templates)

            # Find commented ref attribute within Asset's bracket range
            commented_attrs = self.find_commented_attributes_in_range(
                node.value.lineno,
                node.value.end_lineno,
                'ref'
            )
            kwargs['ref'] = tuple(commented_attrs['ref'])

            # Build MetaAsset
            try:
                asset = MetaAsset(**kwargs)
            except TypeError as e:
                raise TypeError(
                    f"Asset '{asset_name}' at line {start_line}: {e}"
                ) from e
            assets.append(asset)

        return assets
