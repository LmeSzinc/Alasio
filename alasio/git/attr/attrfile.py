import re
from typing import List

import msgspec

from alasio.ext.cache import cached_property
from alasio.git.attr.pathregex import gitattributes_to_regex


class FileAttrs(msgspec.Struct, dict=True):
    # "module/xxx.py"
    # path sep can only be "/" in git
    path: str
    # list of PatternBase object
    pending_pattern: "List[PatternBase]" = msgspec.field(default_factory=list)

    @cached_property
    def filename(self):
        # get name
        path = self.path
        if '/' in path:
            _, _, name = path.rpartition('/')
            return name
        return path

    @cached_property
    def attrs_dict(self) -> dict:
        """Calculate final attrs dict by gitattributes override rules.

        Patterns from deeper directories override those from parent directories.
        Within the same .gitattributes file, later patterns override earlier ones.
        Sort pending_pattern by root depth (shallow first, deep later),
        so deeper patterns merge after and override shallower ones.
        """
        result = {}
        # sort by root depth: "" (depth 0) first, "frontend/" (depth 1) later, etc.
        sorted_patterns = sorted(
            self.pending_pattern,
            key=lambda p: p.root.count('/')
        )
        for pattern in sorted_patterns:
            for key, value in pattern.data.items():
                # -value means unset (reset to unspecified)
                if value == '-':
                    result.pop(key, None)
                else:
                    result[key] = value
        return result


class PatternBase(msgspec.Struct, dict=True):
    # folder of .gitattributes file
    # "" for /.gitattributes, "src/" for /src/.gitattributes
    root: str
    # attrs_dict from parse_gitattributes_line()
    data: dict

    def apply(self, files: "list[FileAttrs]"):
        raise NotImplementedError


class PatternRegex(PatternBase):
    # fallback method for any pathspec
    pathspec: str

    @cached_property
    def regex(self):
        regex = gitattributes_to_regex(self.root, self.pathspec)
        return re.compile(regex)

    def apply(self, files: "list[FileAttrs]"):
        regex = self.regex
        root = self.root
        if root:
            for f in files:
                if f.path.startswith(root) and regex.match(f.path):
                    f.pending_pattern.append(self)
        else:
            for f in files:
                if regex.match(f.path):
                    f.pending_pattern.append(self)


class PatternFileName(PatternBase):
    # for pathspec like "package.json"
    name: str

    def apply(self, files: "list[FileAttrs]"):
        name = self.name
        root = self.root
        if root:
            for f in files:
                if f.path.startswith(root) and f.filename == name:
                    f.pending_pattern.append(self)
        else:
            for f in files:
                if f.filename == name:
                    f.pending_pattern.append(self)

    @classmethod
    def match_pathspec(cls, pathspec: str):
        # pathspec should not contain any gitattributes grammar, just filename
        if '/' in pathspec:
            return False
        if '*' in pathspec:
            return False
        if '[' in pathspec:
            return False
        return True


class PatternFileSuffix(PatternBase):
    # for pathspec like "*.py"
    # pathspec like "*.svelte.ts", "*file.txt" is not included
    suffix: str

    def apply(self, files: "list[FileAttrs]"):
        suffix = self.suffix
        root = self.root
        if root:
            for f in files:
                if f.path.startswith(root) and f.filename.endswith(suffix):
                    f.pending_pattern.append(self)
        else:
            for f in files:
                if f.filename.endswith(suffix):
                    f.pending_pattern.append(self)

    @classmethod
    def match_pathspec(cls, pathspec: str):
        if '/' in pathspec:
            return False
        if not pathspec.startswith('*.'):
            return False
        suffix = pathspec[2:]
        if '*' in suffix:
            return False
        if '.' in suffix:
            return False
        if '[' in suffix:
            return False
        return True


class PatternAny(PatternBase):
    # for pathspec="*"

    def apply(self, files: "list[FileAttrs]"):
        root = self.root
        if root:
            for f in files:
                if f.path.startswith(root):
                    f.pending_pattern.append(self)
        else:
            for f in files:
                f.pending_pattern.append(self)

    @classmethod
    def match_pathspec(cls, pathspec: str):
        return pathspec == '*'


def get_pattern(root, pathspec, attrs_dict) -> PatternBase:
    """
    Args:
        root (str):
        pathspec (str):
        attrs_dict (dict[str, str]): See parse_gitattributes_line()
    """
    if PatternFileSuffix.match_pathspec(pathspec):
        return PatternFileSuffix(root=root, data=attrs_dict, suffix=pathspec[1:])
    if PatternFileName.match_pathspec(pathspec):
        return PatternFileName(root=root, data=attrs_dict, name=pathspec)
    if PatternAny.match_pathspec(pathspec):
        return PatternAny(root=root, data=attrs_dict)
    return PatternRegex(root, data=attrs_dict, pathspec=pathspec)
