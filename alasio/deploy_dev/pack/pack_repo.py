from hashlib import sha1
from typing import Union

from tqdm import tqdm

from alasio.backport import removesuffix
from alasio.deploy.pack.pack_model import FileInfo, RefInfo
from alasio.deploy_dev.pack.encode_base import PackEncodeBase
from alasio.ext.cache import cached_property
from alasio.ext.compress.algo_lzma import lzma_compress
from alasio.ext.compress.algo_zstd import zstd_compress
from alasio.ext.path import PathStr
from alasio.git.attr.attr import GitAttributes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.git.repo import GitRepo
from alasio.logger import logger


class PackFull(PackEncodeBase):
    def __init__(self, repo: Union[GitRepo, MockGitRepo], commit=''):
        """
        Args:
            repo (GitRepo): GitRepo object
            commit (str): commit sha1 in str
        """
        super().__init__()
        self.repo = repo
        if commit:
            self.latest_commit = commit
        if not self.latest_commit:
            self.latest_commit = repo.head_get()
        if not self.latest_commit:
            raise ValueError(f'Empty latest commit at repo {repo}')

    @staticmethod
    def _load_git_mode(mode, path=''):
        """
        Convert git entry mode to mode value (0 for 644, 1 for 755)

        Args:
            mode (bytes): Git entry mode
            path (str): File path for warning messages

        Returns:
            int: mode value (0 for filemode 644, 1 for filemode 755)
        """
        if mode == b'100644':
            return 0
        elif mode == b'100755':
            return 1
        elif mode == b'120000':
            logger.warning(f'FileInfo does not support symlink yet, file="{path}"')
            return 0
        else:
            # 040000 and 160000 should be handled by list_files() so nothing should hit here
            logger.warning(f'FileInfo gets unknown git entry mode {mode}, file="{path}"')
            return 0

    @staticmethod
    def _load_data(file, data, source=None, zstd=True):
        """
        Find the best compress algorithm to store data, and set fields on file_info

        Args:
            file (FileInfo): FileInfo object to update
            data (bytes): File content
            source (bytes): Optional old file content for zstd patch-from
            zstd (bool): Whether to try zstd compression
        """
        best_length = len(data)
        # empty file, treat as raw
        if best_length == 0:
            file.algo = 0
            file.data = data
            file.data_size = 0
            file.size = 0
            file.sha1 = ''
            return
        best_data = data
        algo = 0

        # try lzma compression
        compressed_data = lzma_compress(data)
        compressed_length = len(compressed_data)
        if compressed_length < best_length:
            best_length = compressed_length
            best_data = compressed_data
            algo = 1
        else:
            del compressed_length
            del compressed_data

        if zstd:
            # try zstd --patch-from
            if source is not None:
                compressed_data = zstd_compress(data, source=source)
                compressed_length = len(compressed_data)
                if compressed_length < best_length:
                    best_length = compressed_length
                    best_data = compressed_data
                    algo = 2
                else:
                    del compressed_length
                    del compressed_data

            # try plain zstd compression
            compressed_data = zstd_compress(data, source=source)
            compressed_length = len(compressed_data)
            if compressed_length < best_length:
                best_length = compressed_length
                best_data = compressed_data
                algo = 2
            else:
                del compressed_length
                del compressed_data

        # set
        file.algo = algo
        file.data = best_data
        file.data_size = best_length
        file.size = len(data)
        file.sha1 = sha1(data).hexdigest()

    @staticmethod
    def _new_deleted(path):
        """
        Create an empty record to indicate a file that should not exist

        Args:
            path (str):

        Returns:
            FileInfo:
        """
        return FileInfo(path=path, edit=2)

    @cached_property
    def filelist(self):
        """
        {filepath: FileEntry}
        """
        return self.repo.list_files(self.latest_commit)

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
            # use git sha1 temporarily
            info = FileInfo(path=path, sha1=entry.sha1, size=len(obj.decoded))
            info.mode = self._load_git_mode(entry.mode, path=path)
            out[tuple(path.split('/'))] = info

            # if folder does not have __init__.py, add __init__.py and mark as deleted
            # this prevent running unknown code, because python will auto import __init__.py
            if path.endswith('.py'):
                parent = path
                while True:
                    parent = parent.uppath()
                    if not parent:
                        break
                    init = parent.joinpath('__init__.py')
                    key = tuple(init.split('/'))
                    if key not in out:
                        out[key] = self._new_deleted(init)

        # sort by path, but deeper path goes behind
        # which is like DFS file iterating of parent path
        out = {v.path: v for k, v in sorted(out.items(), key=lambda x: (x[0][:-1], len(x[0]), x))}
        # update EOL
        self._populate_eol(out)
        # convert edit to C (copied)
        self._populate_edit_copied(dict_fileinfo=out)
        # set data, algo, sha1, size, data_size
        self._populate_data(out)
        return out

    def _populate_eol(self, dict_fileinfo: "dict[str, FileInfo]"):
        """
        Apply .gitattributes onto files
        Attributes apply to FileInfo object, so no returns
        """
        fileattrs = self.gitattributes.apply_files(dict_fileinfo)
        repo = self.repo
        for attr in fileattrs:
            # there should be no KeyError
            file = dict_fileinfo[attr.path]
            # skip D (deleted)
            if file.edit == 2:
                continue
            # mode -> text/binary
            mode = attr.attrs_dict.get('text', 'auto')
            if mode == 'set':
                text = True
            elif mode == 'unset':
                text = False
            else:
                # text="auto", decide by content
                content = repo.cat(file.sha1).decoded
                if b'\x00' in content:
                    text = False
                else:
                    text = True
            # set to mode
            if text:
                eol = attr.attrs_dict.get('eol', 'auto')
                if eol == 'crlf':
                    file.eol = 1
                else:
                    file.eol = 0
            else:
                file.eol = 2

    def _populate_edit_copied(
            self,
            dict_refinfo: "dict[str, RefInfo]" = None,
            dict_fileinfo: "dict[str, FileInfo]" = None,
    ):
        """
        Convert edit to C (copied), if file is the same as previous file
        """
        # ref files cannot be C (copied), so index starts at its length
        index = -1
        dict_sha1_to_index = {}
        if dict_refinfo:
            for file in dict_refinfo.values():
                index += 1
                dict_sha1_to_index[file.sha1] = index

        if not dict_fileinfo:
            return
        for file in dict_fileinfo.values():
            index += 1

            # skip D (deleted)
            if file.edit == 2:
                continue
            # empty files are not considered as same
            if file.size == 0:
                continue

            # in full path, file are in edit A (added) or C (copied)
            sha = file.sha1
            if sha in dict_sha1_to_index:
                source_index = dict_sha1_to_index[sha]
                file.edit = 0
                file.source_lookback = index - source_index
                # reuse data info of source file
                file.size = 0
                file.mode = 0
                file.eol = 0
                file.algo = 0
                file.data = b''
                file.data_size = 0
            else:
                pass
            # update dict_known_file in both cases
            # so when having multiple same files, the latter ones can reference the nearest source file
            dict_sha1_to_index[sha] = index

    def _populate_data(self, dict_fileinfo: "dict[str, FileInfo]"):
        """
        load data, find the best compress algorithm
        """
        repo = self.repo
        for file in tqdm(dict_fileinfo.values()):
            # load new files only, A (added)
            if file.edit == 0 and file.source_lookback == 0:
                # load data, full pack use lzma only to avoid producing complex list_algo
                data = repo.cat(file.sha1).decoded
                self._load_data(file, data, zstd=False)
