import msgspec

from alasio.backport import removeprefix, removesuffix
from alasio.ext.algorithm.lcp import get_lcp
from alasio.ext.algorithm.pathlcs import PathLookbackLCS
from alasio.ext.algorithm.pathlcs_v3 import PathLookbackLCSV3
from alasio.ext.cache import cached_property
from alasio.ext.path import PathStr
from alasio.git.attr.attr import GitAttributes
from alasio.git.stage.repo import GitRepo


class FileInfo(msgspec.Struct, dict=True):
    # campaign/event_20260417_cn/sp4.py
    path: str
    size: int
    sha1: str

    # False to ensure file is deleted
    # Since python will load __init__.py by default,
    # this tag would provide consistent behaviour when user side has some random files
    should_exist: bool = True
    # True for text, False for binary
    is_text: bool = True
    # True for eol=CRLF, False for eol=LF
    is_crlf: bool = False


class FileList:
    def __init__(self, root):
        self.root = PathStr.new(root)

    @cached_property
    def repo(self):
        return GitRepo(self.root).read_lazy()

    @cached_property
    def filelist(self):
        """
        {filepath: FileEntry}
        """
        head = self.repo.head_get()
        return self.repo.list_files(head)

    @cached_property
    def gitattributes(self):
        attr = GitAttributes()
        repo = self.repo
        for path, entry in self.filelist.items():
            if path == '.gitattributes':
                obj = repo.cat(entry.sha1)
                content = obj.decoded.decode()
                attr.load(root='', content=content)
            if path.endswith('/.gitattributes'):
                root = removesuffix(path, '.gitattributes')
                obj = repo.cat(entry.sha1)
                content = obj.decoded.decode()
                attr.load(root=root, content=content)
        return attr

    @cached_property
    def fileinfo(self) -> "dict[str, FileInfo]":
        """
        Returns:
            dict[str, FileInfo]: {filepath: FileInfo}
        """
        out = {}
        repo = self.repo
        for path, entry in self.filelist.items():
            obj = repo.cat(entry.sha1)
            path = PathStr(path)
            info = FileInfo(path=path, size=obj.size, sha1=entry.sha1)
            out[tuple(path.split('/'))] = info
            # add __init__.py, mark as size=-1
            if path.endswith('.py'):
                parent = path
                while True:
                    parent = parent.uppath()
                    if not parent:
                        break
                    init = parent.joinpath('__init__.py')
                    if init not in out:
                        info = FileInfo(path=init, size=0, sha1='', should_exist=False)
                        out[tuple(init.split('/'))] = info

        # sort by path, but deeper path goes behind
        # which is like DFS file iterating of parent path
        out = {v.path: v for k, v in sorted(out.items(), key=lambda x: (x[0][:-1], len(x[0]), x))}
        self.apply_attr(out)
        return out

    def apply_attr(self, dict_fileinfo: "dict[str, FileInfo]"):
        """
        Apply .gitattributes onto files
        Attributes apply to FileInfo object, so no returns
        """
        fileattrs = self.gitattributes.apply_files(dict_fileinfo)
        repo = self.repo
        for attr in fileattrs:
            # there should be no KeyError
            info = dict_fileinfo[attr.path]
            if not info.should_exist:
                continue
            # mode -> is_text
            mode = attr.attrs_dict.get('text', 'auto')
            if mode == 'set':
                info.is_text = True
            elif mode == 'unset':
                info.is_text = False
            else:
                # text="auto", decide by content
                content = repo.cat(info.sha1).decoded
                if b'\x00' in content:
                    info.is_text = False
                else:
                    info.is_text = True
            # eol -> is_crlf
            if info.is_text:
                eol = attr.attrs_dict.get('eol', 'auto')
                if eol == 'crlf':
                    info.is_crlf = True
                # else: lf/auto, keep default LF
            # else: binary file, keep default LF

    def show(self):
        """Display file-walk sequence annotated with V1 LCS lookback info."""
        prev = ''
        idx = 0
        v1 = PathLookbackLCS()
        for path, info in self.fileinfo.items():
            prefix = get_lcp(prev, path)
            display = removeprefix(path, prefix)
            prev = info.path
            lookback, length = v1.get_lcs(display, min_length=3, max_length=63)
            v1.add_path(info.path)
            idx += 1
            print(f"{idx:4d}  {lookback:4d} {length:2d}  {display}")

    def compare_v1_v3(self):
        """Run PathLookbackLCS (V1) and PathLookbackLCSV3 (V3) on the same
        file sequence and report every file where the results differ."""
        v1 = PathLookbackLCS()
        v3 = PathLookbackLCSV3()
        prev = ''
        mismatches = []
        total = 0

        for path, info in self.fileinfo.items():
            # print(total, path)
            # LCP should not overlap file suffix
            name, _, _ = path.rpartition('.')
            prefix = get_lcp(prev, name)
            rel_path = removeprefix(path, prefix)
            prev = info.path

            r1 = v1.get_lcs(rel_path, min_length=3, max_length=63)
            r3 = v3.get_lcs(rel_path, min_length=3, max_length=63)

            v1.add_path(info.path)
            v3.add_path(info.path)
            total += 1

            if r1 != r3:
                mismatches.append((path, rel_path, r1, r3))

        if not mismatches:
            print(f"V1 == V3 on all {total} files.")
        else:
            print(f"{len(mismatches)} / {total} files differ:")
            for path, rel, r1, r3 in mismatches:
                print(f"  {path}")
                print(f"    rel={rel!r}")
                print(f"    V1: lookback={r1[0]}, length={r1[1]}  |  V3: lookback={r3[0]}, length={r3[1]}")
                print()


if __name__ == '__main__':
    root = r'E:\ProgramData\Pycharm\StarRailCopilot'
    fl = FileList(root)
    fl.compare_v1_v3()
