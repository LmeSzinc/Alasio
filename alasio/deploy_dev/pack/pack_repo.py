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
from alasio.git.attr.attr import GitAttributes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.git.repo import GitRepo
from alasio.logger import logger


def _dfs_path_key(path):
    """
    DFS path sort key, files of a folder come before its subfolders.

    The folder is compared component wise and the name decides inside one
    folder, which is what sorting by ``path.split('/')`` gives (plus the length
    and the parts as a tie break that can never apply). Splitting every path
    into a tuple to compare the folders costs ~6ms more per version on a 9.4k
    file repo, the key below compares the folder as one string instead: its
    '/' is replaced by NUL, a character below every character a pack path can
    carry (validate_filepath rejects control characters), so the plain string
    order of the folder is its component wise order.

    Args:
        path (str): File path

    Returns:
        tuple: Sort key
    """
    folder, _, name = path.rpartition('/')
    return folder.replace('/', '\x00'), name


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

    # A zstd patch at most 1/SKIP_PLAIN_ZSTD_RATIO of the plain best (the
    # smaller of raw and lzma) can not be beaten by the plain zstd candidate:
    # lzma and zstd-22 both approach the entropy of the content, their measured
    # spread on a real repo is 1.19x at most, while beating such a patch needs
    # plain zstd to be 5x smaller than lzma (measured 144/192 M / RM records
    # skip it, 0 of them would have changed the winner). See
    # doc/2026-09-27_update-pack-from-repo.md section 7.19.
    SKIP_PLAIN_ZSTD_RATIO = 5

    # Inputs below this size always try the plain zstd candidate: the absolute
    # cost is negligible and the size ratio of small contents is noisy.
    SKIP_PLAIN_ZSTD_MIN_SIZE = 1000

    # Zstd level of the pack data candidates, the patch and the plain one. The
    # level is a run wide policy, not a per call one: the pack cache is keyed by
    # the content only, a changed level changes the produced bytes and has to
    # come with a PACK_VERSION bump like any other encoding change.
    ZSTD_LEVEL = 22

    @staticmethod
    def _load_data(file, data, cache_info=None, zstd=True, zstd_source=None):
        """
        Find the best compress algorithm to store data, and set fields on file_info

        The candidates are the plain ones (raw / lzma), the zstd patch-from
        (zstd_source is the dictionary) and the plain zstd of level ZSTD_LEVEL.
        The smaller candidate wins, a tie keeps the earlier one, so the order
        is raw, lzma, patch, plain zstd.

        cache_info is the cached encoding of the content without a dictionary,
        the raw / lzma rule the full pack uses (see PackCache): it stands for
        the plain candidates and saves the lzma compression of the content.

        Args:
            file (FileInfo): FileInfo object to update
            data (bytes): File content
            cache_info (FileInfo | None): Cached plain encoding of the content,
                an entry of another size is not the content and is ignored.
                Defaults to None, the plain candidates are compressed here.
            zstd (bool): Whether to try the plain zstd candidate. Defaults to True.
            zstd_source (bytes | None): Old file content as the zstd dictionary
                of a patch-from candidate. Defaults to None, no patch is tried.

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

        if cache_info is not None and cache_info.size == len(data):
            # the content is already compressed with the raw / lzma rule, its
            # sha1 is the content sha1 of this very content: no need to hash
            # or compress it again
            best_data = cache_info.data
            best_length = cache_info.data_size
            algo = cache_info.algo
            content_sha1 = cache_info.sha1
        else:
            best_data = data
            algo = 0
            content_sha1 = None

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
        # the plain best, the bar every remaining candidate has to beat and the
        # reference of the skip below
        plain_length = best_length

        patch_used = False
        # try zstd --patch-from
        if zstd_source is not None:
            compressed_data = zstd_compress(
                data, source=zstd_source, level=PackFull.ZSTD_LEVEL)
            compressed_length = len(compressed_data)
            if compressed_length < best_length:
                best_length = compressed_length
                best_data = compressed_data
                algo = 2
                patch_used = True
            else:
                del compressed_length
                del compressed_data

        # try plain zstd, skipped when a far smaller patch won: both plain
        # compressors approach the entropy of the content (the measured spread
        # is 1.19x at most), so plain zstd can not be 5x smaller than lzma and
        # can not beat such a patch. Small contents keep the full candidate
        # set, their size ratio is noisy.
        if zstd and not (
                patch_used
                and len(data) >= PackFull.SKIP_PLAIN_ZSTD_MIN_SIZE
                and plain_length > best_length * PackFull.SKIP_PLAIN_ZSTD_RATIO
        ):
            compressed_data = zstd_compress(data, level=PackFull.ZSTD_LEVEL)
            compressed_length = len(compressed_data)
            if compressed_length < best_length:
                best_length = compressed_length
                best_data = compressed_data
                algo = 2
                # the plain data does not need the dictionary: a caller that
                # kept the old file as a patch source must drop it
                patch_used = False
            else:
                del compressed_length
                del compressed_data

        # set
        file.algo = algo
        file.data = best_data
        file.data_size = best_length
        file.size = len(data)
        if content_sha1 is not None:
            file.sha1 = content_sha1
        else:
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
        """
        Rule engine of the version, built from its .gitattributes files

        The contents are registered, not parsed (see GitAttributes.register),
        and their digest is taken in the same pass: the registered files are
        dropped once the rules are parsed, gitattributes_fingerprint is taken
        here.

        Returns:
            GitAttributes:
        """
        attr = GitAttributes()
        repo = self.repo
        digest = sha1()
        digest.update(self.pack_version)
        for path, entry in self.filelist.items():
            if path == '.gitattributes':
                root = ''
            elif path.endswith('/.gitattributes'):
                root = removesuffix(path, '.gitattributes')
            else:
                continue
            attr.register(root=root, content=repo.cat(entry.sha1).decoded)
            # git already hashed the content: the blob sha1 is the identity of
            # the file, the digest does not hash the bytes again
            digest.update(root.encode())
            digest.update(b'\x00')
            digest.update(entry.sha1.encode())
            digest.update(b'\x00')
        self._gitattributes_digest = digest.hexdigest()
        return attr

    @cached_property
    def gitattributes_fingerprint(self):
        """
        Identity of the .gitattributes files of the version.

        The attributes of a path (what its eol is decided from) only depend on
        the .gitattributes files of the version, so the versions that carry the
        same files share the table of PackCache.eol. The fingerprint is the
        digest that gitattributes took while it registered the files: the pack
        format version (the resolution belongs to the format, another format
        resolves in a table of its own) followed by the (root, git blob sha1) of
        every file of the version in the order of the filelist (git hashed the
        content already, the digest does not hash the bytes again; the order of
        two files of one depth can not apply to the same path, so it does not
        matter, a changed order only costs another table). A change of any file
        gives another digest and another table, whatever the other files of the
        version are. See PackFull._populate_eol.

        Returns:
            str: Hex sha1 digest of the .gitattributes files
        """
        # taken by gitattributes: the registered files are dropped once the
        # rules are parsed, the digest has to be taken while they are all known
        _ = self.gitattributes
        return self._gitattributes_digest

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
            # use git sha1 temporarily
            info = FileInfo(path=path, sha1=bytes.fromhex(entry.sha1), size=len(obj.decoded))
            info.mode = self._load_git_mode(entry.mode, path=path)
            out[path] = info

            # if folder does not have __init__.py, add __init__.py and mark as deleted
            # this prevent running unknown code, because python will auto import __init__.py
            if path.endswith('.py'):
                # the path is normalized and a name can not carry a '/'
                # (list_files joins the names itself), so rpartition is uppath()
                folder, sep, _ = path.rpartition('/')
                while sep:
                    init = f'{folder}/__init__.py'
                    if init not in out:
                        out[init] = self._new_deleted(init)
                    folder, sep, _ = folder.rpartition('/')

        # sort by path, but deeper path goes behind
        # which is like DFS file iterating of parent path
        out = {path: out[path] for path in sorted(out, key=_dfs_path_key)}
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

        Resolving the attributes of a path (the rule engine) is the expensive
        part, the eol is then decided by the attributes and, when text is
        "auto", by the content. Both change rarely, so they are cached by
        PackFull.gitattributes_fingerprint in PackCache.eol: a version resolves
        the paths the earlier versions did not have and sniffs the contents they
        did not see, every other record is a dict lookup.
        """
        cache = self.cache
        if cache is None:
            # no cache, resolve in a table of this version
            dict_eol = {}
        else:
            dict_eol = cache.eol.setdefault(self.gitattributes_fingerprint, {})
        # a D (deleted) record is not a file of the version, it keeps the
        # default eol of FileInfo
        files = [file for file in dict_fileinfo.values() if file.edit != 2]
        missing = [file.path for file in files if file.path not in dict_eol]
        if missing:
            # only the paths unknown to the .gitattributes state of the version
            # need the rule engine
            for attr in self.gitattributes.apply_files(missing):
                dict_eol[attr.path] = attr.attrs_dict
        hit = len(files) - len(missing)
        miss = len(missing)
        repo = self.repo
        for file in files:
            # there should be no KeyError
            attrs_dict = dict_eol[file.path]
            # mode -> text/binary
            mode = attrs_dict.get('text', 'auto')
            # set when the content decides, the eol is then cached under it
            key = None
            if mode == 'set':
                text = True
            elif mode == 'unset':
                text = False
            else:
                # text="auto", decide by content. The eol of a content is
                # cached under (filepath, git blob sha1): an unchanged content
                # skips the read and the sniff, a changed content is resolved
                # and cached as another entry. The tuple key can not clash
                # with the path keys of the attributes
                key = (file.path, file.sha1)
                eol = dict_eol.get(key)
                if eol is not None:
                    file.eol = eol
                    hit += 1
                    continue
                content = repo.cat(file.sha1.hex()).decoded
                if b'\x00' in content:
                    text = False
                else:
                    text = True
            # set to mode
            if text:
                eol = attrs_dict.get('eol', 'auto')
                if eol == 'crlf':
                    file.eol = 1
                else:
                    file.eol = 0
            else:
                file.eol = 2
            if key is not None:
                # remember the eol of this content, see the lookup above
                dict_eol[key] = file.eol
                miss += 1
        if cache is not None:
            cache.mark_many('eol', hit, miss)

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

        The encoding of a generated file is a pure function of
        (version, filepath), it is taken from the cache and stored there, see
        _extra_cache_info.

        Returns:
            dict[str, FileInfo]: {filepath: FileInfo}
        """
        extra = {}
        for path, content in self.extra_content.items():
            info = FileInfo(path=path, eol=2)
            cache_info = PackFull._extra_cache_info(
                self.cache, self.current_version, path, content)
            self._load_data(info, content, cache_info=cache_info, zstd=False)
            extra[path] = info
        return extra

    @staticmethod
    def _extra_cache_info(cache, version, path, data):
        """
        Cached raw / lzma encoding of a generated extra file

        The extra files of a version (the index pack, the commit history) are
        generated bytes, not files of the repo, so they have no git blob sha1
        to key a content entry by. Their plain encoding is a pure function of
        (version, filepath) and is keyed by that instead: it is stored once
        and reused by every update pack of the run, whose new side is always
        the same version. See doc/2026-09-27_update-pack-from-repo.md 7.21.

        Args:
            cache (PackCache | None): Cache of the run, None to skip it
            version (str): Version the extra file is generated for
            path (str): Filepath of the extra file
            data (bytes): Generated content, compressed on a cache miss

        Returns:
            FileInfo | None: Cached raw / lzma encoding, None without a cache
        """
        if cache is None:
            return None
        key = (version, path)
        entry = cache.extra.get(key)
        if entry is None:
            entry = FileInfo(path=path)
            PackFull._load_data(entry, data, zstd=False)
            cache.extra[key] = entry
            cache.mark('extra', hit=False)
        else:
            cache.mark('extra', hit=True)
        return entry

    def _populate_data(self, dict_fileinfo: "dict[str, FileInfo]"):
        """
        load data, find the best compress algorithm

        The encoded data of a content is taken from the cache when the version
        being packed shares it with an already packed version, the git blob
        sha1 is the key so a hit costs no blob read at all. See PackCache.
        """
        repo = self.repo
        cache = self.cache
        # git blob sha1 (hex) -> (content sha1, size), used to fix up the C
        # (copied) records, see the loop below
        content_of_blob = {}
        for file in tqdm(dict_fileinfo.values()):
            # load new files only, A (added)
            if file.edit == 0 and file.source_lookback == 0:
                # the git blob sha1, as the hex str the git tree carries,
                # _load_data() replaces file.sha1 with the content sha1
                git_sha1 = file.sha1.hex()
                entry = cache.content.get(git_sha1) if cache is not None else None
                cached = entry.index if entry is not None else None
                if cached is None:
                    # load data, full pack use lzma only to avoid producing complex list_algo
                    data = repo.cat(git_sha1).decoded
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
                source = content_of_blob.get(file.sha1.hex())
                if source is not None:
                    file.sha1, file.size = source
