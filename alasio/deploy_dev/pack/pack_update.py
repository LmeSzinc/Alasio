"""
Generate update packs from an old full pack to a new full pack.

An update pack lets a client upgrade its local working tree from the
old version to the new version incrementally:

- refinfo records the old files that the update reads (rename / copy
  sources and zstd patch dictionaries), carrying their size and sha1 so
  the client can verify them before use
- fileinfo records the changes to apply: A (added), C (copied), M
  (modified), D (deleted), R (renamed), RM (renamed + modified)
- index_update is a zstd patch-from from the old index pack bytes to
  the new index pack bytes: the client decompresses it with its local
  .pack/index.pack as the dictionary and atomically replaces the file,
  so the local index always equals the front part of the full pack of
  the matching version

The file changes are computed by PackDiff (see pack_diff.py): a git
diff-like comparison with zstd dictionary based rename detection.
refinfo order follows the old pack decode order (old.idx_info), a
convention shared with the client's local old index.
"""

from alasio.deploy.pack.pack_model import FileInfo, IdxInfo, RefInfo
from alasio.deploy_dev.pack.encode_base import PackEncodeBase
from alasio.deploy_dev.pack.pack_diff import PackDiff
from alasio.ext.cache import cached_property
from alasio.ext.compress.algo_zstd import zstd_compress


class PackUpdate(PackEncodeBase):
    """
    Generate an update pack that upgrades the old pack to the new pack.

    The old and new packs must be full packs (refinfo empty, index
    update part empty, data section present), the typical input of the
    server pipeline that publishes a new release.
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
            old (PackDecodeBase): Full pack of the old version
            new (PackDecodeBase): Full pack of the new version
            min_similarity (float): Minimum similarity for rename
                detection, 0~1. Defaults to 0.5, like git's default
                50% rename threshold.
            max_size_ratio (float): Maximum size ratio of rename
                candidates, pairs outside [1/ratio, ratio] are never
                matched. Defaults to 4.0.
            zstd_level (int): Zstd level for pack data and index update
                compression. Defaults to 22.
            similarity_level (int): Zstd level for rename similarity
                scoring, a fast level is enough for the score. Defaults
                to 3.

        Raises:
            ValueError: If old or new is not a full pack, or a parameter
                is out of range
        """
        super().__init__()
        if not old._has_data or not new._has_data:
            raise ValueError('PackUpdate requires full packs with a data section, got a pack without one')
        if old.refinfo or new.refinfo:
            raise ValueError('PackUpdate requires full packs, got a pack with refinfo (update pack)')
        if old._index_update or new._index_update:
            raise ValueError('PackUpdate requires full packs, got a pack with an index update part (update pack)')
        self.old = old
        self.new = new
        self.latest_commit = new.version
        self.zstd_level = zstd_level
        self._diff = PackDiff(
            old,
            new,
            min_similarity=min_similarity,
            max_size_ratio=max_size_ratio,
            zstd_level=zstd_level,
            similarity_level=similarity_level,
        )

    # ════════════════════════════════════════════════════════════════════════
    #  diff
    # ════════════════════════════════════════════════════════════════════════

    @cached_property
    def diff_info(self) -> "dict[str, IdxInfo]":
        """
        File changes from the old version to the new version.

        See PackDiff.diff_info for the record semantics.

        Returns:
            dict[str, IdxInfo]: {path: IdxInfo}
        """
        return self._diff.diff_info

    # ════════════════════════════════════════════════════════════════════════
    #  pack
    # ════════════════════════════════════════════════════════════════════════

    @cached_property
    def refinfo(self) -> "dict[str, RefInfo]":
        """
        Old file records referenced by the update pack.

        Files that the client needs to verify and read to apply the
        update: the sources of M (patch) / R / RM / C records. The
        order follows the old pack decode order (old.idx_info), a
        convention shared with the client's local old index.

        Returns:
            dict[str, RefInfo]: {filepath: RefInfo}

        Raises:
            ValueError: If a referenced old file is missing from the old pack
        """
        ref_paths = self._diff.ref_paths
        out = {}
        for info in self.old.idx_info:
            if info.edit != 2 and info.path in ref_paths:
                out[info.path] = RefInfo(path=info.path, size=info.size, sha1=info.sha1)
        missing = ref_paths - set(out)
        if missing:
            raise ValueError(f'Failed to build refinfo: missing old files: {sorted(missing)}')
        return out

    @cached_property
    def fileinfo(self) -> "dict[str, FileInfo]":
        """
        New file records of the update pack, sorted like PackFull.

        source_lookback is the distance to the referenced record in the
        merged refinfo + fileinfo sequence, computed from source_path.
        C records have their meta reset like the full pack, the decoder
        restores it from the source record.

        Returns:
            dict[str, FileInfo]: {filepath: FileInfo}

        Raises:
            ValueError: If a source record is missing or not earlier
                in the merged sequence
        """
        diff = self.diff_info
        ref = self.refinfo
        ref_index = {path: i for i, path in enumerate(ref)}
        records = sorted(diff.values(), key=PackDiff._sort_key)
        file_index = {}
        out = {}
        for i, info in enumerate(records):
            index = len(ref) + i
            if info.edit != 2:
                if info.source_path:
                    source_index = ref_index.get(info.source_path)
                    if source_index is None:
                        # the source is an earlier new file
                        try:
                            source_index = file_index[info.source_path]
                        except KeyError:
                            raise ValueError(
                                f'Failed to build fileinfo: source of {info.path} not found: '
                                f'{info.source_path}'
                            ) from None
                    info.source_lookback = index - source_index
                    if info.source_lookback <= 0:
                        raise ValueError(
                            f'Failed to build fileinfo: source of {info.path} is not earlier'
                        )
                else:
                    info.source_lookback = 0
                file_index[info.path] = index
            out[info.path] = FileInfo(
                path=info.path, edit=info.edit, eol=info.eol, mode=info.mode,
                algo=info.algo, data=info.data, data_size=info.data_size,
                size=info.size, sha1=info.sha1, source_lookback=info.source_lookback,
            )
        return out

    @cached_property
    def index_update(self) -> bytes:
        """
        zstd compressed data to update the local index pack.

        The data is a zstd patch-from from the old index pack bytes to
        the new index pack bytes. The client decompresses it with its
        local .pack/index.pack as the dictionary and atomically
        replaces the file, so the local index always equals the front
        part of the full pack of the matching version.

        Returns:
            bytes: zstd compressed data
        """
        old_index = self.old.extract_index_pack()
        new_index = self.new.extract_index_pack()
        return zstd_compress(new_index, source=old_index, level=self.zstd_level)
