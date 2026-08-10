"""
Compare the fileinfo of two versions, produce the diff records.

The comparison follows a git diff-like flow:

1. files in both versions with the same sha1 / mode / eol are
   unchanged and left out of the diff
2. files in both versions with different content become M (modified)
   records, their data is a zstd patch-from of the old blob
3. files only in the new version are matched against files only in the
   old version to detect renames: pairs with the same blob sha1 are
   pure renames (R), other pairs are scored by zstd dictionary
   compression (see PackDiff.similarity), pairs above min_similarity
   become R / RM records, unmatched new files become A (added),
   unmatched old files become D (deleted)
4. A records whose content already exists in an unchanged old file or
   an earlier new file become C (copied) records, referencing the
   source instead of carrying data

All content is compared and patched in the git blob form (LF
normalized, no checkout line ending): the zstd patch of an M / RM
record is compressed from the old blob to the new blob, and the client
must normalize its working tree file to LF (by the old record's eol)
before using it as the decompression dictionary.

The blob content is read through the decoder's catdata and verified
against the record, so the diff logic works on any decoder-like object:
PackDecodeBase, or MockDecodeBase in tests.
"""
from hashlib import sha1

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.pack_model import IdxInfo
from alasio.ext.cache import cached_property
from alasio.ext.compress.algo_lzma import lzma_compress
from alasio.ext.compress.algo_zstd import zstd_compress


class PackDiff:
    """
    Compare the decoders of two versions, expose the diff records.

    The input is the decoder of the old version and the decoder of the
    new version (PackDecodeBase or MockDecodeBase, providing idx_info
    and catdata), the output is diff_info ({path: IdxInfo}) and
    ref_paths (the old files referenced by the diff).
    """

    def __init__(
            self,
            old,
            new,
            min_similarity=0.5,
            max_size_ratio=4.0,
            zstd_level=22,
            similarity_level=3,
    ):
        """
        Args:
            old (PackDecodeBase | MockDecodeBase): Decoder of the old
                version, full pack
            new (PackDecodeBase | MockDecodeBase): Decoder of the new
                version, full pack
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

        Raises:
            ValueError: If a parameter is out of range
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
        # blob content caches (git blob form, LF normalized), keyed by path
        self._old_blob_cache: "dict[str, bytes]" = {}
        self._new_blob_cache: "dict[str, bytes]" = {}
        # files that exist, deleted markers (edit=2) are excluded
        self._real_old = {info.path: info for info in old.idx_info if info.edit != 2}
        self._real_new = {info.path: info for info in new.idx_info if info.edit != 2}

    @cached_property
    def diff_info(self) -> "dict[str, IdxInfo]":
        """
        File changes from the old version to the new version.

        Keyed by the new path, deleted records are keyed by the old
        path. source_path points to the old file that the record
        references: the old file of the same path for M with patch
        data, the rename source for R / RM, the copied file for C. It
        is empty for A / D and for M records with plain data.

        Returns:
            dict[str, IdxInfo]: {path: IdxInfo}

        Raises:
            PackDecodeError: If a file fails to load from a decoder
        """
        real_old = self._real_old
        real_new = self._real_new

        out = {}
        # modified in place
        for path in real_old.keys() & real_new.keys():
            old_info = real_old[path]
            new_info = real_new[path]
            if self._is_unchanged(old_info, new_info):
                continue
            info = IdxInfo(path=path, edit=1, eol=new_info.eol, mode=new_info.mode)
            if self._load_modified(info, old_info, new_info):
                info.source_path = path
            else:
                # plain data wins, the old file is not referenced
                info.source_path = ''
            out[path] = info

        # rename detection between files only in the old version and files only in the new version
        renames = self._find_renames(real_old, real_new)
        renamed_old = set(renames.values())

        # deleted
        for path in real_old.keys() - real_new.keys() - renamed_old:
            out[path] = self._new_deleted(path)

        # renamed and added
        for path, old_path in renames.items():
            old_info = real_old[old_path]
            new_info = real_new[path]
            if old_info.sha1 == new_info.sha1 and old_info.eol == new_info.eol:
                # pure rename, the content is identical, no data needed
                info = IdxInfo(path=path, edit=3, eol=new_info.eol, mode=new_info.mode)
                info.source_path = old_path
                info.size = new_info.size
                info.sha1 = new_info.sha1
                info.data = b''
                out[path] = info
            else:
                # rename and modify, data is a zstd patch from the old file
                info = IdxInfo(path=path, edit=3, eol=new_info.eol, mode=new_info.mode)
                info.source_path = old_path
                if not self._load_modified(info, old_info, new_info):
                    # plain compression beats the patch, add + delete instead
                    info.edit = 0
                    info.source_path = ''
                    out[old_path] = self._new_deleted(old_path)
                out[path] = info
        for path in real_new.keys() - real_old.keys() - renames.keys():
            info = IdxInfo(path=path, edit=0, eol=real_new[path].eol, mode=real_new[path].mode)
            self._load_added(info, real_new[path])
            out[path] = info

        # content dedup: A records whose content already exists are copied
        self._populate_edit_copied(out, real_old, real_new)
        return out

    @cached_property
    def ref_paths(self) -> "set[str]":
        """
        Old file paths referenced by the diff records.

        These paths must appear in the refinfo of the update pack: the
        sources of M (patch) / R / RM records and the copied old files.
        A copied record whose source is a new file (an earlier record
        of the new version) is not a ref path.

        Returns:
            set[str]: Old file paths referenced by the diff
        """
        diff = self.diff_info
        unchanged = set(self._real_old) & set(self._real_new) - set(diff)
        out = set()
        for info in diff.values():
            if not info.source_path:
                continue
            if info.edit == 1:
                # M records only reference the old file when patch data is used
                out.add(info.source_path)
            elif info.edit == 3:
                # R / RM records always reference the old file
                out.add(info.source_path)
            elif info.source_path in unchanged:
                # copied from an unchanged old file
                out.add(info.source_path)
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

        Args:
            info (IdxInfo): Record to load, edit must be M or RM
            old_info (IdxInfo): Old record, the patch source
            new_info (IdxInfo): New record

        Returns:
            bool: True if the zstd patch-from data was stored, the old
                file is then referenced by the record
        """
        new_blob = self._read_blob(self._new_blob_cache, self.new, new_info)
        old_blob = self._read_blob(self._old_blob_cache, self.old, old_info) if old_info.sha1 else b''
        return self._load_data_best(info, new_blob, source=old_blob or None)

    def _load_added(self, info, new_info):
        """
        Load the data of an added file, the best of raw / lzma / zstd.

        Args:
            info (IdxInfo): Record to load, edit must be A
            new_info (IdxInfo): New record
        """
        new_blob = self._read_blob(self._new_blob_cache, self.new, new_info)
        self._load_data_best(info, new_blob, source=None)

    def _read_blob(self, cache, decoder, info):
        """
        Read the git blob content of a file from a decoder, cached by path.

        The pack stores git blob content (LF normalized for text files),
        the content is verified against the record's size and sha1.

        Args:
            cache (dict[str, bytes]): Blob cache of the decoder
            decoder (PackDecodeBase | MockDecodeBase): Decoder to read from
            info (IdxInfo): Record of the file

        Returns:
            bytes: Blob content

        Raises:
            PackDecodeError: If the content fails to decode or verify
        """
        blob = cache.get(info.path)
        if blob is None:
            data = decoder.catdata(info)
            if info.algo:
                data = PackDecodeBase._decompress(info, data)
            PackDecodeBase._check_content(info, data)
            blob = bytes(data)
            cache[info.path] = blob
        return blob

    def _load_data_best(self, info, data, source):
        """
        Store the best of raw / lzma / zstd patch-from / plain zstd data.

        Mirrors PackFull._load_data, additionally reports whether the
        zstd patch-from data won so the caller knows whether the old
        file must be referenced. Empty data is stored raw.

        Args:
            info (IdxInfo): Record to update
            data (bytes): File content to store, the new blob
            source (bytes, optional): Old blob as the zstd patch-from
                dictionary

        Returns:
            bool: True if the zstd patch-from data won
        """
        best_length = len(data)
        # empty file, treat as raw
        if best_length == 0:
            info.algo = 0
            info.data = data
            info.data_size = 0
            info.size = 0
            info.sha1 = ''
            return False
        best_data = data
        algo = 0
        patch_used = False

        # try lzma compression
        compressed_data = lzma_compress(data)
        if len(compressed_data) < best_length:
            best_length = len(compressed_data)
            best_data = compressed_data
            algo = 1

        # try zstd patch-from
        if source is not None:
            compressed_data = zstd_compress(data, source=source, level=self.zstd_level)
            if len(compressed_data) < best_length:
                best_length = len(compressed_data)
                best_data = compressed_data
                algo = 2
                patch_used = True

        # try plain zstd compression
        compressed_data = zstd_compress(data, level=self.zstd_level)
        if len(compressed_data) < best_length:
            best_length = len(compressed_data)
            best_data = compressed_data
            algo = 2

        # set
        info.algo = algo
        info.data = best_data
        info.data_size = best_length
        info.size = len(data)
        info.sha1 = sha1(data).hexdigest()
        return patch_used

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
                        new_blob = self._read_blob(self._new_blob_cache, self.new, new_info)
                    old_blob = self._read_blob(self._old_blob_cache, self.old, old_info)
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

    def _populate_edit_copied(self, diff_info, real_old, real_new):
        """
        Convert A records to C (copied) records when the content already exists.

        A new file whose content matches an unchanged old file (kept in
        the new version) or an earlier new file references the source
        instead of carrying data.

        The source is limited to LF files (eol=0) with mode 0: the
        decoder restores the meta of a copied record from its source
        record, and a refinfo entry only carries size and sha1, so a
        copy from a CRLF or 755 old file cannot be represented
        correctly. Records in the new fileinfo keep their own meta, so
        copies between new files only require equal eol and mode.

        Args:
            diff_info (dict[str, IdxInfo]): Diff records to update
            real_old (dict[str, IdxInfo]): Old files that exist
            real_new (dict[str, IdxInfo]): New files that exist
        """
        # unchanged old files, candidates for copy sources
        unchanged = set(real_old) & set(real_new) - set(diff_info)
        # {sha1: (source path, restored eol, restored mode)}
        source_map = {}
        for info in self.old.idx_info:
            if (
                    info.path in unchanged
                    and info.edit != 2
                    and info.sha1
                    and info.eol == 0
                    and info.mode == 0
            ):
                # the restored meta of a refinfo copy is eol=0 mode=0
                source_map.setdefault(info.sha1, (info.path, 0, 0))

        records = sorted(diff_info.values(), key=self._sort_key)
        for record in records:
            eol = record.eol
            mode = record.mode
            if record.edit == 0 and record.sha1:
                source = source_map.get(record.sha1)
                if source is not None:
                    source_path, source_eol, source_mode = source
                    if eol == source_eol and mode == source_mode:
                        # copied, the data is not stored in the pack
                        record.source_path = source_path
                        record.size = 0
                        record.eol = 0
                        record.mode = 0
                        record.algo = 0
                        record.data = b''
                        record.data_size = 0
                        # the decoder restores the meta from the source
                        eol = source_eol
                        mode = source_mode
            # the record can be a copy source for later files
            if record.edit != 2 and record.sha1:
                source_map[record.sha1] = (record.path, eol, mode)

    @staticmethod
    def _sort_key(info):
        """
        Sort records like PackFull.fileinfo: by parent path, then depth, then path.

        Args:
            info (IdxInfo): Record to sort

        Returns:
            tuple: Sort key
        """
        path = tuple(info.path.split('/'))
        return path[:-1], len(path), path

    @staticmethod
    def _new_deleted(path):
        """
        Create an empty record to indicate a file that should not exist

        Args:
            path (str):

        Returns:
            IdxInfo:
        """
        info = IdxInfo(path=path, edit=2)
        info.data = b''
        return info
