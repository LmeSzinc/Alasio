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


class PatternBase(msgspec.Struct):
    # folder of .gitattributes file
    # "" for /.gitattributes, "src/" for /src/.gitattributes
    root: str
    # attrs_dict from parse_gitattributes_line()
    data: dict


class PatternRegex(PatternBase, dict=True):
    # fallback method for any pathspec
    pathspec: str

    @cached_property
    def regex(self):
        regex = gitattributes_to_regex(self.root, self.pathspec)
        return re.compile(regex)

    def apply(self, files: "list[FileAttrs]"):
        regex = self.regex
        root = self.root
        for file in files:
            path = file.path
            if root and not path.startswith(root):
                continue
            if not regex.match(path):
                continue
            # apply
            file.pending_pattern.append(self)


class PatternFileName(PatternBase):
    # for pathspec like "package.json"
    name: str

    def apply(self, files: "list[FileAttrs]"):
        name = self.name
        root = self.root
        for file in files:
            path = file.path
            if root and not path.startswith(root):
                continue
            # get name
            if '/' in path:
                _, _, path = path.rpartition('/')
            if path != name:
                continue
            # apply
            file.pending_pattern.append(self)

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
    suffix: str

    def apply(self, files: "list[FileAttrs]"):
        suffix = self.suffix
        root = self.root
        for file in files:
            path = file.path
            if root and not path.startswith(root):
                continue
            # get name
            if '/' in path:
                _, _, path = path.rpartition('/')
            if not path.endswith(suffix):
                continue
            # apply
            file.pending_pattern.append(self)

    @classmethod
    def match_pathspec(cls, pathspec: str):
        # pathspec should be "*abc"
        if '/' in pathspec:
            return False
        if not pathspec.startswith('*'):
            return False
        suffix = pathspec[1:]
        if '*' in suffix:
            return False
        if '[' in suffix:
            return False
        return True


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
    return PatternRegex(root, data=attrs_dict, pathspec=pathspec)
