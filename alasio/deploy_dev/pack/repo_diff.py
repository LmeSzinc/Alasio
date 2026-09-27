"""
Compare the fileinfo of two versions, produce the diff records.

The diff records are built step by step:

1. rename: files only in the old version are matched against files
   only in the new version, the matched pairs become R (renamed)
   records when the content is identical, or RM (renamed + modified)
   records with a zstd patch otherwise; an RM whose patch is not
   worthwhile becomes A + D instead
2. the records of the new version (renamed, added, modified) follow
   the DFS path order of the new pack (same as pack_repo), the added
   and modified records are converted to C (copied) records when
   their content already exists in an unchanged old file or an
   earlier record
3. deleted: the remaining files only in the old version become D
   (deleted) records, they come last

All content is compared and patched in the git blob form (LF
normalized, no checkout line ending): the zstd patch of an M / RM
record is compressed from the old blob to the new blob, and the client
must normalize its working tree file to LF (by the old record's eol)
before using it as the decompression dictionary.

The blob content is read from the git repo the versions were built from,
looked up by the blob sha1 of the version's file list: the diff never reads
or decompresses a stored pack, it always works on the git blob form (LF
normalized for text files), the form the packs store and the form a client
has in its working tree.
"""
from alasio.deploy.pack.pack_model import FileInfo, RefInfo
from alasio.deploy_dev.pack.pack_cache import ContentCache, PatchCache
from alasio.deploy_dev.pack.pack_repo import PackFull, _dfs_path_key
from alasio.ext.cache import cached_property
from alasio.ext.compress.algo_zstd import zstd_compress


class UpdateInfo(FileInfo):
    """
    A record of a file change in the update pack.

    The diff records are built in memory, so unlike IdxInfo the data
    is always present (bytes, not an unset marker) and there is no
    data_start offset. source_path is set by the diff logic: the old
    file of the same path for M with patch data, the rename source
    for R / RM, the copied file for C, empty for A / D and for M
    records with plain data.
    """
    # path of reffile
    # real value will be calculated from `source_lookback` in decoding
    source_path: str = ''


class RepoDiff:
    """
    Compare two versions of a git repo, expose the diff records.

    The versions are PackFull objects built from the same git repo (the pack
    server path, no stored full pack is needed), the git repo comes from the
    new version (PackFull.repo), the output is diff_info ({path: UpdateInfo})
    and refinfo (the old file records referenced by the diff).

    A PackCache can be passed to reuse the encodings across the versions of a
    run: the A records are keyed by content, the M / RM patches by content
    pair, see PackCache and doc/2026-09-27_update-pack-from-repo.md.
    """

    def __init__(
            self,
            old,
            new,
            min_similarity=0.5,
            max_size_ratio=4.0,
            zstd_level=22,
            similarity_level=3,
            cache=None,
    ):
        """
        Args:
            old (PackFull): Old version, built from the git repo
            new (PackFull): New version, built from the git repo
            min_similarity (float): Minimum similarity for rename
                detection, 0~1. Defaults to 0.5, like git's default
                50% rename threshold.
            max_size_ratio (float): Maximum size ratio of rename
                candidates, pairs outside [1/ratio, ratio] are never
                matched. Defaults to 4.0.
            zstd_level (int): Zstd level for data compression. Defaults
                to 22.
            similarity_level (int): Zstd level for rename similarity
                scoring, a fast level is enough for the score. Defaults
                to 3.
            cache (PackCache, optional): Cache shared by the versions of a
                run, None to encode everything without cache. Defaults to None.

        Raises:
            ValueError: If a parameter is out of range, or the new version
                carries no git repo
        """
        if not 0 <= min_similarity < 1:
            raise ValueError(f'min_similarity must be in [0, 1), got {min_similarity}')
        if max_size_ratio < 1:
            raise ValueError(f'max_size_ratio must be >= 1, got {max_size_ratio}')
        self.old = old
        self.new = new
        self.min_similarity = min_similarity
        self.max_size_ratio = max_size_ratio
        self.zstd_level = zstd_level
        self.similarity_level = similarity_level
        self.cache = cache
        # the versions are of the same git repo and the contents are read from
        # it, the repo caches the objects it read so no extra cache is needed
        self.repo = getattr(new, 'repo', None)
        if self.repo is None:
            raise ValueError('RepoDiff requires the git repo, use a PackFull as the new version')
        # files that exist, deleted markers (edit=2) are excluded
        self._real_old = {info.path: info for info in old.idx_info if info.edit != 2}
        self._real_new = {info.path: info for info in new.idx_info if info.edit != 2}

    @cached_property
    def diff_info(self) -> "dict[str, UpdateInfo]":
        """
        File changes from the old version to the new version.

        The records are built step by step: the records of the new
        version (rename R / RM, copied A / C, edit M / C) follow the
        DFS path order of the new pack (same as pack_repo), then the
        deleted (D) records come last. The copy detection runs while
        the records are built, so a file modified to match an existing
        file is recognized as a copy instead of carrying patch data.

        The records follow the DFS path order of the new pack, so a
        copied record always finds its source in an earlier record
        and the update pack needs no extra sort.

        Keyed by the new path, deleted records are keyed by the old
        path. source_path points to the old file that the record
        references: the old file of the same path for M with patch
        data, the rename source for R / RM, the copied file for C. It
        is empty for A / D and for M records with plain data.

        Returns:
            dict[str, UpdateInfo]: {path: UpdateInfo}

        Raises:
            PackDecodeError: If a file fails to load from a decoder
        """
        real_old = self._real_old
        real_new = self._real_new
        # files that stay identical in both versions
        unchanged = {
            path for path in real_old.keys() & real_new.keys()
            if self._is_unchanged(real_old[path], real_new[path])
        }
        # {sha1: source path} for copy detection, only the content matters
        source_map = {}
        for info in self.old.idx_info:
            if info.path in unchanged and info.edit != 2 and info.sha1:
                source_map.setdefault(info.sha1, info.path)

        out = {}

        # 1. rename: match files only in the old version with files only
        # in the new version, the matched records are built below in the
        # new pack order
        renames = self._find_renames(real_old, real_new)
        renamed_old = set(renames.values())

        # 2. records of the new version: renamed (R / RM), added (A / C)
        # and modified (M / C) records follow the DFS path order of the
        # new pack (same as pack_repo), so a copied record always finds
        # its source in an earlier record
        added = real_new.keys() - real_old.keys() - renames.keys()
        modified = (real_old.keys() & real_new.keys()) - unchanged
        downgraded_old = set()
        for path in sorted(real_new.keys(), key=_dfs_path_key):
            new_info = real_new[path]
            if path in renames:
                old_path = renames[path]
                old_info = real_old[old_path]
                if old_info.sha1 == new_info.sha1 and old_info.eol == new_info.eol:
                    # pure rename, the content is identical, no data needed
                    record = UpdateInfo(path=path, edit=3, eol=new_info.eol, mode=new_info.mode)
                    record.source_path = old_path
                    record.size = new_info.size
                    record.sha1 = new_info.sha1
                    record.data = b''
                    out[path] = record
                else:
                    # rename and modify, data is a zstd patch from the old file
                    record = UpdateInfo(path=path, edit=3, eol=new_info.eol, mode=new_info.mode)
                    record.source_path = old_path
                    if not self._load_modified(record, old_info, new_info):
                        # plain compression beats the patch, add + delete instead
                        record.edit = 0
                        record.source_path = ''
                        downgraded_old.add(old_path)
                        if record.sha1:
                            # the downgraded record is an A record, it joins
                            # the copy detection like other added records
                            self._try_copy(record, source_map)
                            source_map[record.sha1] = path
                    out[path] = record
            elif path in added:
                record = UpdateInfo(path=path, edit=0, eol=new_info.eol, mode=new_info.mode)
                self._load_added(record, new_info)
                if record.sha1:
                    self._try_copy(record, source_map)
                    source_map[record.sha1] = path
                out[path] = record
            elif path in modified:
                old_info = real_old[path]
                record = UpdateInfo(path=path, edit=1, eol=new_info.eol, mode=new_info.mode)
                if self._load_modified(record, old_info, new_info):
                    record.source_path = path
                else:
                    # plain data wins, the old file is not referenced
                    record.source_path = ''
                if record.sha1:
                    self._try_copy(record, source_map)
                    source_map[record.sha1] = path
                out[path] = record

        # 3. deleted: files only in the old version become D records,
        # including the sources of renames downgraded to add + delete
        deleted = (real_old.keys() - real_new.keys() - renamed_old) | downgraded_old
        for info in self.old.idx_info:
            if info.path in deleted:
                out[info.path] = self._new_deleted(info.path)
        return out

    @cached_property
    def refinfo(self) -> "dict[str, RefInfo]":
        """
        Old file records referenced by the diff records.

        These records must appear in the refinfo of the update pack:
        the sources of M (patch) / R / RM records and the copied old
        files. A copied record whose source is a new file (an earlier
        record of the new version) is not a ref record.

        The order follows the DFS path sort of pack_repo (old.idx_info
        in production), a convention shared with the client's local
        old index.

        Returns:
            dict[str, RefInfo]: {filepath: RefInfo}

        Raises:
            ValueError: If a referenced old file is missing from the
                old pack
        """
        diff = self.diff_info
        unchanged = set(self._real_old) & set(self._real_new) - set(diff)
        ref_paths = set()
        for info in diff.values():
            if not info.source_path:
                continue
            if info.edit == 1:
                # M records only reference the old file when patch data is used
                ref_paths.add(info.source_path)
            elif info.edit == 3:
                # R / RM records always reference the old file
                ref_paths.add(info.source_path)
            elif info.source_path in unchanged:
                # copied from an unchanged old file
                ref_paths.add(info.source_path)
        missing = ref_paths - set(self._real_old)
        if missing:
            raise ValueError(f'Failed to build refinfo: missing old files: {sorted(missing)}')
        out = {}
        for path in sorted(ref_paths, key=_dfs_path_key):
            old_info = self._real_old[path]
            out[path] = RefInfo(path=path, size=old_info.size, sha1=old_info.sha1)
        return out

    @staticmethod
    def _is_unchanged(old_info, new_info):
        """
        Check if a file is identical in both versions.

        Args:
            old_info (IdxInfo): Old record
            new_info (IdxInfo): New record

        Returns:
            bool: True if the file is unchanged and can be left out
                of the diff
        """
        return (
            old_info.sha1 == new_info.sha1
            and old_info.mode == new_info.mode
            and old_info.eol == new_info.eol
        )

    def _load_modified(self, info, old_info, new_info):
        """
        Load the data of a modified file, a zstd patch from the old file.

        The best of raw / lzma / zstd patch-from / plain zstd data is
        stored. The patch-from wins for similar contents, the expected
        case of M and RM records.

        The encoding only depends on the content pair (old, new), so a version
        that changes a file back and forth, or a version that shares the old
        content with another one, takes the bytes from the cache instead of
        compressing again. See PackCache.patch.

        Args:
            info (UpdateInfo): Record to load, edit must be M or RM
            old_info (IdxInfo): Old record, the patch source
            new_info (IdxInfo): New record

        Returns:
            bool: True if the zstd patch-from data was stored, the old
                file is then referenced by the record
        """
        if not old_info.sha1:
            # an empty old file has no content to use as the zstd dictionary,
            # the encoding is the one of an added file
            self._load_added(info, new_info)
            return False
        cache = self.cache
        key = (old_info.sha1, new_info.sha1)
        cached = cache.patch.get(key) if cache is not None else None
        if cached is not None:
            info.algo, info.size, info.data_size, info.sha1, info.data = (
                cached.info.algo, cached.info.size, cached.info.data_size,
                cached.info.sha1, cached.info.data)
            cache.mark('patch', hit=True)
            return cached.patch_used
        patch_used = self._load_modified_data(info, old_info, new_info)
        if cache is not None:
            cache.patch[key] = PatchCache(FileInfo(
                path=info.path, algo=info.algo, size=info.size,
                data_size=info.data_size, sha1=info.sha1, data=info.data), patch_used)
            cache.mark('patch', hit=False)
        return patch_used

    def _load_modified_data(self, info, old_info, new_info):
        """
        Compress the data of a modified file, without the cache

        Args:
            info (UpdateInfo): Record to load, edit must be M or RM
            old_info (IdxInfo): Old record, the patch source
            new_info (IdxInfo): New record

        Returns:
            bool: True if the zstd patch-from data was stored
        """
        new_blob = self._read_new_blob(new_info)
        old_blob = self._read_old_blob(old_info)
        algo_name = PackFull._load_data(info, new_blob, source=old_blob or None, level=self.zstd_level)
        return algo_name == 'zstd_patch'

    def _load_added(self, info, new_info):
        """
        Load the data of an added file, the best of raw / lzma / zstd.

        The same file is an added record of every version of the lookback
        window that does not have it yet, so the encoding is cached by the git
        blob sha1 of the file, the same key the version rebuilds use. See
        PackCache.content.

        Args:
            info (UpdateInfo): Record to load, edit must be A
            new_info (IdxInfo): New record
        """
        cache = self.cache
        git_sha1 = None
        entry = None
        if cache is not None:
            file_entry = self.new.filelist.get(new_info.path)
            if file_entry is not None:
                # the cache is keyed by the git blob sha1, like PackFull does
                git_sha1 = bytes.fromhex(file_entry.sha1)
                entry = cache.content.get(git_sha1)
        cached = entry.update if entry is not None else None
        if cached is not None:
            info.algo, info.size, info.data_size, info.sha1, info.data = (
                cached.algo, cached.size, cached.data_size, cached.sha1, cached.data)
            cache.mark('content', hit=True)
            return
        new_blob = self._read_new_blob(new_info)
        PackFull._load_data(info, new_blob, source=None, level=self.zstd_level)
        if cache is not None and git_sha1 is not None:
            if entry is None:
                entry = cache.content[git_sha1] = ContentCache()
            entry.update = FileInfo(
                path=info.path, algo=info.algo, size=info.size,
                data_size=info.data_size, sha1=info.sha1, data=info.data)
            cache.mark('content', hit=False)

    def _read_old_blob(self, info):
        """
        Read the git blob content of an old file.

        Args:
            info (IdxInfo): Record of the file

        Returns:
            bytes: Blob content

        Raises:
            ValueError: If the version has no such file
        """
        return self._read_git_blob(self.old, info)

    def _read_new_blob(self, info):
        """
        Read the git blob content of a new file.

        Args:
            info (IdxInfo): Record of the file

        Returns:
            bytes: Blob content

        Raises:
            ValueError: If the version has no such file
        """
        return self._read_git_blob(self.new, info)

    def _read_git_blob(self, source, info):
        """
        Read the git blob content of a file of a version.

        The content comes from the git repo the versions were built from, looked
        up by the blob sha1 of the version's file list: no stored pack is read
        and nothing is decompressed, the repo itself caches the objects it read.
        The diff always works on the git blob form (LF normalized for text
        files), which is the form the packs store and the form a client has in
        its working tree.

        Args:
            source (PackFull): Version to read from
            info (IdxInfo): Record of the file

        Returns:
            bytes: Blob content

        Raises:
            ValueError: If the version has no such file and the record is not
                a generated extra file
        """
        entry = source.filelist.get(info.path)
        if entry is not None:
            return self.repo.cat(entry.sha1).decoded
        # a generated extra file (e.g. .pack/history.pack) is not a file of the
        # version, the pack holds the generated content
        blob = source.extra_content.get(info.path)
        if blob is None:
            raise ValueError(
                f'Failed to read the content of {info.path}: not a file of the version')
        return blob

    def _find_renames(self, real_old, real_new):
        """
        Match files only in the old version with files only in the new version.

        Pairs with the same blob sha1 are exact renames, their
        similarity is 1. Other pairs are filtered by size ratio and
        scored by zstd dictionary compression (see similarity).
        Candidates above min_similarity are matched greedily one-to-one
        by descending similarity: every old file is the source of at
        most one rename, because an R / RM record moves the old file.

        Args:
            real_old (dict[str, IdxInfo]): Old files that exist
            real_new (dict[str, IdxInfo]): New files that exist

        Returns:
            dict[str, str]: {new path: old path} of matched renames
        """
        deleted = [path for path, info in real_old.items() if path not in real_new and info.sha1]
        added = [path for path, info in real_new.items() if path not in real_old and info.sha1]

        candidates = []
        for new_path in added:
            new_info = real_new[new_path]
            new_blob = None
            for old_path in deleted:
                old_info = real_old[old_path]
                if old_info.sha1 == new_info.sha1:
                    # exact content match, similarity is 1
                    sim = 1.0
                else:
                    # size pre-filter before compressing the pair
                    ratio = new_info.size / old_info.size
                    if not (1 / self.max_size_ratio <= ratio <= self.max_size_ratio):
                        continue
                    if new_blob is None:
                        new_blob = self._read_new_blob(new_info)
                    old_blob = self._read_old_blob(old_info)
                    sim = self.similarity(old_blob, new_blob, level=self.similarity_level)
                if sim >= self.min_similarity:
                    candidates.append((sim, new_path, old_path))

        # greedy one-to-one matching by descending similarity
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        renames = {}
        matched_old = set()
        for sim, new_path, old_path in candidates:
            if new_path in renames or old_path in matched_old:
                continue
            renames[new_path] = old_path
            matched_old.add(old_path)
        return renames

    @staticmethod
    def similarity(old_content, new_content, level=3):
        """
        Estimate the similarity of two file contents with zstd dict compression.

        The new content is compressed with the old content as the zstd
        dictionary, the smaller the patch the more similar the contents.
        similarity = 1 - len(patch) / len(new_content), so identical
        contents score ~1 and unrelated contents score ~0. This is not
        a real git diff, but zstd patch-from is fast in Python and the
        ratio is a good proxy of the fraction of content that stays the
        same.

        Args:
            old_content (bytes): Old file content, as the zstd dictionary
            new_content (bytes): New file content, must not be empty
            level (int): Zstd compression level for the score. Defaults
                to 3, a fast level is enough for a score.

        Returns:
            float: Similarity in [0, 1], higher is more similar
        """
        patch = zstd_compress(new_content, source=old_content, level=level)
        return 1 - len(patch) / len(new_content)

    def _try_copy(self, info, source_map):
        """
        Convert a record to a copied record when its content already exists.

        A record whose content matches an unchanged old file (kept in
        the new version) or an earlier record references the source
        instead of carrying data: a new file that duplicates an
        existing file, a modified file whose new content matches an
        existing file, or a modified file whose new content matches
        another modified file.

        Only the content matters: the converted record keeps its own
        eol / mode, encoded in the pack, so a copy across eol or mode
        differences is exact. The size / sha1 / data attributes are
        restored from the source record by the decoder.

        Args:
            info (UpdateInfo): Record to convert
            source_map (dict[str, str]): {sha1: source path}
        """
        if not info.sha1:
            # empty files are not considered as copies
            return
        source_path = source_map.get(info.sha1)
        if source_path is None:
            return
        # copied, the data is not stored in the pack
        info.edit = 0
        info.source_path = source_path

    @staticmethod
    def _new_deleted(path):
        """
        Create an empty record to indicate a file that should not exist

        Args:
            path (str):

        Returns:
            UpdateInfo:
        """
        info = UpdateInfo(path=path, edit=2)
        info.data = b''
        return info
