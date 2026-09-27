from hashlib import sha1
from typing import Union

from tqdm import tqdm

from alasio.backport import removesuffix
from alasio.deploy.pack.pack_model import FileInfo, RefInfo
from alasio.deploy_dev.history.encode_history import encode_commit_history
from alasio.deploy_dev.pack.encode_base import PackEncodeBase
from alasio.deploy_dev.pack.pack_cache import ContentCache
from alasio.ext.cache import cached_property
from alasio.ext.compress.algo_lzma import lzma_compress
from alasio.ext.compress.algo_zstd import zstd_compress
from alasio.ext.path import PathStr
from alasio.git.attr.attr import GitAttributes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.git.repo import GitRepo
from alasio.logger import logger


def _dfs_path_key(path):
    """
    DFS path sort key, files of a folder come before its subfolders.

    Args:
        path (str): File path

    Returns:
        tuple: Sort key
    """
    parts = tuple(path.split('/'))
    return (parts[:-1], len(parts), parts)


class PackFull(PackEncodeBase):
    def __init__(self, repo: Union[GitRepo, MockGitRepo], commit='', cache=None, pack_version=None):
        """
        Args:
            repo (GitRepo): GitRepo object
            commit (str): commit sha1 in str, the version of the pack
            cache (PackCache, optional): Cache shared by the versions of a run,
                None to encode everything without cache. Defaults to None.
            pack_version (bytes, optional): Pack format version to encode with.
                Defaults to None, the current version of PackEncodeBase; an
                already published pack must be rebuilt with the format version
                it was encoded with, see PackUpdate
        """
        super().__init__()
        self.repo = repo
        self.cache = cache
        if pack_version is not None:
            self.pack_version = pack_version
        # the version of a full pack is the commit being packed
        if commit:
            self.current_version = commit
        if not self.current_version:
            self.current_version = repo.head_get()
        if not self.current_version:
            raise ValueError(f'Empty latest commit at repo {repo}')
        # the commit every file is read from, current_version records it
        self.commit = self.current_version

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
    def _load_data(file, data, source=None, zstd=True, level=22):
        """
        Find the best compress algorithm to store data, and set fields on file_info

        Args:
            file (FileInfo): FileInfo object to update
            data (bytes): File content
            source (bytes | None): Optional old file content for zstd patch-from
            zstd (bool): Whether to try zstd compression
            level (int): Zstd level for zstd compression. Defaults to 22.

        Returns:
            str: algo name of the stored data, 'raw' / 'lzma' / 'zstd' /
                'zstd_patch'. 'zstd_patch' means the zstd patch-from
                data won, the caller must keep the old file as the
                decompression dictionary
        """
        best_length = len(data)
        # empty file, treat as raw
        if best_length == 0:
            file.algo = 0
            file.data = data
            file.data_size = 0
            file.size = 0
            file.sha1 = b''
            return 'raw'
        best_data = data
        algo = 0
        patch_used = False

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
                compressed_data = zstd_compress(data, source=source, level=level)
                compressed_length = len(compressed_data)
                if compressed_length < best_length:
                    best_length = compressed_length
                    best_data = compressed_data
                    algo = 2
                    patch_used = True
                else:
                    del compressed_length
                    del compressed_data

            # try plain zstd compression
            compressed_data = zstd_compress(data, level=level)
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
        file.sha1 = sha1(data).digest()
        if patch_used:
            return 'zstd_patch'
        return ('raw', 'lzma', 'zstd')[algo]

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
        return self.repo.list_files(self.commit)

    @cached_property
    def idx_info(self) -> "list[FileInfo]":
        """
        Records of the version in the encoded order, like a decoder's idx_info.

        A PackFull is a diff source of the pack server (see RepoDiff): the
        pipeline builds the records from the git repo instead of decoding a
        stored pack, so the diff of two versions needs no full pack on disk.

        Returns:
            list[FileInfo]: All records in the encoded order
        """
        return list(self.fileinfo.values())

    @cached_property
    def index_pack(self) -> bytes:
        """
        Index pack bytes of the version, header plus index section.

        The pipeline needs the index pack of every version it packs: it is the
        current index of the version's full pack and the old index of every
        update pack that starts from the version. It is assembled once for each
        PackFull, and an instance is built once for each version of a run.

        Returns:
            bytes: Index pack bytes
        """
        return b''.join(self.iter_packidx_data())

    @cached_property
    def gitattributes(self):
        attr = GitAttributes()
        repo = self.repo
        for path, entry in self.filelist.items():
            if path == '.gitattributes':
                obj = repo.cat(entry.sha1)
                content = bytes(obj.decoded).decode()
                attr.load(root='', content=content)
            if path.endswith('/.gitattributes'):
                root = removesuffix(path, '.gitattributes')
                obj = repo.cat(entry.sha1)
                content = bytes(obj.decoded).decode()
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
            info = FileInfo(path=path, sha1=bytes.fromhex(entry.sha1), size=len(obj.decoded))
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
        out = {v.path: v for k, v in sorted(out.items(), key=lambda x: _dfs_path_key(x[1].path))}
        # update EOL
        self._populate_eol(out)
        # convert edit to C (copied)
        self._populate_edit_copied(dict_fileinfo=out)
        # set data, algo, sha1, size, data_size
        self._populate_data(out)
        # add the extra files, e.g. the history of the latest commits
        out.update(self.extra_fileinfo)
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
                content = repo.cat(file.sha1.hex()).decoded
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
                # reuse data info of source file, eol / mode stay the
                # values of this file, encoded in the pack
                file.size = 0
                file.algo = 0
                file.data = b''
                file.data_size = 0
            else:
                pass
            # update dict_known_file in both cases
            # so when having multiple same files, the latter ones can reference the nearest source file
            dict_sha1_to_index[sha] = index

    @cached_property
    def extra_content(self) -> "dict[str, bytes]":
        """
        Raw content of the extra files, generated by the pack itself.

        Extras are synthetic files that are not files of the version being
        packed, e.g. the history of the latest commits, so that the unpacked
        project has the commit history of the packed version. The content is
        generated here, encoded into records by extra_fileinfo, and read by
        RepoDiff as the patch source of the records: the client holds the same
        generated file in its local tree.

        Returns:
            dict[str, bytes]: {filepath: content}
        """
        return {
            '.pack/history.pack': encode_commit_history(
                self.repo.list_commit_have(self.commit, have_lookback=20)),
        }

    @cached_property
    def extra_fileinfo(self) -> "dict[str, FileInfo]":
        """
        Records of the extra files, encoded from extra_content.

        The records carry their own data and are appended after the version
        files, so they do not take part in the copy detection of the version
        files. A record stands for the generated bytes, not for a file of the
        version, so it is binary (eol=2).

        Returns:
            dict[str, FileInfo]: {filepath: FileInfo}
        """
        extra = {}
        for path, content in self.extra_content.items():
            info = FileInfo(path=path, eol=2)
            self._load_data(info, content, zstd=False)
            extra[path] = info
        return extra

    def _populate_data(self, dict_fileinfo: "dict[str, FileInfo]"):
        """
        load data, find the best compress algorithm

        The encoded data of a content is taken from the cache when the version
        being packed shares it with an already packed version, the git blob
        sha1 is the key so a hit costs no blob read at all. See PackCache.
        """
        repo = self.repo
        cache = self.cache
        # git blob sha1 -> (content sha1, size), used to fix up the C (copied)
        # records, see the loop below
        content_of_blob = {}
        for file in tqdm(dict_fileinfo.values()):
            # load new files only, A (added)
            if file.edit == 0 and file.source_lookback == 0:
                # the git blob sha1, _load_data() replaces file.sha1 with the
                # content sha1
                git_sha1 = file.sha1
                entry = cache.content.get(git_sha1) if cache is not None else None
                cached = entry.index if entry is not None else None
                if cached is None:
                    # load data, full pack use lzma only to avoid producing complex list_algo
                    data = repo.cat(git_sha1.hex()).decoded
                    self._load_data(file, data, zstd=False)
                    if cache is not None:
                        if entry is None:
                            entry = cache.content[git_sha1] = ContentCache()
                        entry.index = FileInfo(
                            path=file.path, algo=file.algo, size=file.size,
                            data_size=file.data_size, sha1=file.sha1, data=file.data)
                        cache.mark('content', hit=False)
                else:
                    file.algo, file.size, file.data_size, file.sha1, file.data = (
                        cached.algo, cached.size, cached.data_size, cached.sha1, cached.data)
                    cache.mark('content', hit=True)
                content_of_blob[git_sha1] = (file.sha1, file.size)

        # A C (copied) record carries the info of its source when decoding and
        # no data at all, so the encoder never needed its own values. A PackFull
        # is also used as a diff source (see RepoDiff), where the records are
        # compared by content: restore sha1 / size from the source content, so
        # that copies compare equal to the file they duplicate.
        for file in dict_fileinfo.values():
            if file.edit == 0 and file.source_lookback:
                source = content_of_blob.get(file.sha1)
                if source is not None:
                    file.sha1, file.size = source
