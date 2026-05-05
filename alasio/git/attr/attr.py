from typing import Iterable

from alasio.ext import env
from alasio.ext.cache import cached_property
from alasio.ext.path.calc import get_suffix
from alasio.git.attr.attrfile import FileAttrs, PatternBase, PatternFileName, PatternFileSuffix, get_pattern
from alasio.git.attr.attrline import parse_gitattributes_line


class GitAttributes:
    def __init__(self):
        self.patterns: "list[PatternBase]" = []

    def _load_content(self, root, content, is_builtin=False):
        """
        Args:
            root (str):
            content (str):
            is_builtin:
        """
        for row in content.splitlines():
            row = row.strip()
            if not row or row.startswith('#'):
                continue
            pathspec, attrs_dict = parse_gitattributes_line(row)
            if not pathspec or not attrs_dict:
                continue
            if not is_builtin:
                # ignore wildcard set like: * text=auto eol=lf
                # to prevent repo-level gitattributes override defaults entirely
                # because most .gitattributes file would have this
                if pathspec == '*':
                    continue

            pattern = get_pattern(root, pathspec, attrs_dict)
            # if isinstance(pattern, PatternRegex):
            #     print(pattern.pathspec)
            self.patterns.append(pattern)

    @cached_property
    def _load_builtin(self):
        file = env.ALASIO_ROOT.joinpath('.gitattributes')
        content = file.atomic_read_text()
        self._load_content(root='', content=content, is_builtin=True)
        return None

    def load(self, root, content):
        """
        Args:
            root (str): folder of .gitattributes file
                "" for /.gitattributes, "src/" for /src/.gitattributes
            content (str): File content
        """
        # cache builtin first
        _ = self._load_builtin
        # load
        self._load_content(root=root, content=content)

    def apply_files(self, list_filepath: "Iterable[str]") -> "list[FileAttrs]":
        """
        Args:
            list_filepath: List of filepath, path sep can only be "/" in git
                The input list_filepath needs to be deduplicated first
        """
        # cache builtin first
        _ = self._load_builtin

        files = [FileAttrs(file) for file in list_filepath]

        # apply_files should be equivalent to:
        # for pattern in self.patterns:
        #     pattern.apply(files)
        # but for performance we build cache for most commonly used patterns
        # to avoid matching in O(file_count*pattern_count) complexity

        dict_name: "dict[str, list[FileAttrs]]" = {}
        dict_suffix: "dict[str, list[FileAttrs]]" = {}
        for file in files:
            # build cache for filename
            name = file.filename
            if name in dict_name:
                dict_name[name].append(file)
            else:
                dict_name[name] = [file]
            # build cache for suffix
            suffix = get_suffix(name)
            if suffix in dict_suffix:
                dict_suffix[suffix].append(file)
            else:
                dict_suffix[suffix] = [file]

        for pattern in self.patterns:
            # fast path for most pattern
            typ = type(pattern)
            if typ is PatternFileSuffix:
                cand = dict_suffix.get(pattern.suffix)
                if cand:
                    pattern.apply(cand)
                # else: no files with such suffix
                continue
            if typ is PatternFileName:
                cand = dict_name.get(pattern.name)
                if cand:
                    pattern.apply(cand)
                # else: no files in such name
                continue
            # default apply
            pattern.apply(files)

        return files
