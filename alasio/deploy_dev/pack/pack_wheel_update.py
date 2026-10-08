"""
Build the update pack between the install trees of two wheels.

An update pack lets a client upgrade the files of a distribution from the old
version to the new one without downloading the new full pack:

    new wheel ─┐
               ├─ WheelDiff ─▶ A / C / M / D records ─▶ update pack
    old wheel ─┘                + the ledger record      (index + data)

PackWheelUpdate is the wheel sibling of PackUpdate, the update encoder of the
git pipeline: the version part records both display versions (a non-empty old
version is what makes the pack an update pack), the records are computed by a
diff of the two install trees (WheelDiff, no git repo is involved) and the
record list is assembled, ordered and folded into the records of the pack by
the same code the git pipeline uses (PackUpdate.diff_info / refinfo /
fileinfo). The old side of an update is a wheel, not a stored full pack: the
bytes of a wheel are the input of the channel and the install tree of a
version is a pure function of them, so no materialized staging of an old
version has to be kept around.

The index pack .pack/index.pack is treated as a normal file of the update,
like PackUpdate does: the record is an M record from the old index pack bytes
(the ledger the clients hold) to the new ones, and the old index is recorded
in the refinfo, so a client whose local ledger is not the one the old version
published is detected and downloads the index pack from the server. The wire
paths stay the canonical .pack namespace of the pack format: the client maps
them into the ledger folder of its target (see JobBase.local_path), the pack
carries no dist-key.

The diff does not detect renames, see WheelDiff: the files of a .dist-info
folder change their name with the version of the folder, and the files below
the folder are matched by the path under it (WheelDiff.dist_info_pair), so a
version bump patches them from the old file (M) or copies the ones that did not
change (C, e.g. a license) and only the folder name moves; the folders of the
other files are never matched, a renamed module stays an A + D pair.

Usage:
    from alasio.deploy_dev.pack.pack_wheel import PackWheel
    from alasio.deploy_dev.pack.pack_wheel_update import PackWheelUpdate
    from alasio.ext.path.atomic import atomic_write_stream

    update = PackWheelUpdate(PackWheel(new_wheel), PackWheel(old_wheel))
    atomic_write_stream(f'packdep/httpx/{update.current_version}/from_{update.old_version}.pack',
                        update.iter_pack_data())
"""

from alasio.backport import removesuffix
from alasio.deploy.pack.pack_model import FileInfo, RefInfo
from alasio.deploy_dev.pack._pack_cache import PatchCache, PlainCache
from alasio.deploy_dev.pack.encode_base import PackEncodeBase
from alasio.deploy_dev.pack.pack_full import PackFull, _dfs_path_key, apply_encoding
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.pack.repo_diff import UpdateInfo
from alasio.ext.cache import cached_property


class WheelDiff:
    """
    Compare the install trees of two wheels, produce the diff records.

    The records are the ones an update pack carries (see pack_update):

    - a file the new version does not have becomes a D (deleted) record
    - a file only the new version has becomes an A (added) record, or a C
      (copied) record when its content already exists in an unchanged old
      file or in an earlier record of the new version
    - a file both versions have, with another content or mode, becomes an
      M (modified) record: the best of raw / lzma / zstd patch-from / plain
      zstd is stored, and the record references the old file when the zstd
      patch won (the client decompresses the patch with the old content as
      the dictionary)

    The records of the changed files follow the DFS path order of the new
    version (the order of PackWheel.tree), the deleted records come last, so
    a copied record always references an earlier record and the update pack
    needs no extra sort -- RepoDiff keeps the same invariant for the git
    pipeline.

    The comparison covers the files of the two install trees only: the D
    (deleted) markers of a version (the ``__init__.py`` of a folder that
    ships none, see PackWheel.fileinfo) are a property of the version rather
    than a change between two of them, so they are not records of the diff --
    the client gets them from the new ledger the update installs, and the
    validation that follows the update enforces them.

    Unlike RepoDiff there is no rename detection: the name of every file of
    a .dist-info folder changes with the version of the folder, the design
    accepts the A + D records that come out of it (see the module
    docstring), and the zstd similarity score of the git diff has no
    consumer here: the package files of a distribution rarely move between
    two versions, and when one does, the A + D pair is the honest record of
    it (no guessed rename can be wrong).

    The encodings are reused across the builds of a run through the cache of
    the new version (WHEEL_CACHE by default): an A record is keyed by the
    content sha1, an M record by the (old content, new content) pair, and
    the plain candidates of a content live on the entry of the content, see
    plain_entry. A record that is not the first of a run to carry a content
    takes the bytes from the cache instead of compressing again.

    Attributes:
        old (PackWheel): Old version
        new (PackWheel): New version
        cache (PackCache): Cache of the encodings, the one of the new version
    """

    def __init__(self, old, new):
        """
        Args:
            old (PackWheel): Old version, the install tree the update
                upgrades from
            new (PackWheel): New version, the install tree the update
                upgrades to

        Raises:
            ValueError: If old or new is not a PackWheel
        """
        if not isinstance(old, PackWheel):
            raise ValueError(
                f'WheelDiff requires a PackWheel of the old version, got {type(old).__name__}')
        if not isinstance(new, PackWheel):
            raise ValueError(
                f'WheelDiff requires a PackWheel of the new version, got {type(new).__name__}')
        self.old = old
        self.new = new
        self.cache = new.cache

    @cached_property
    def dist_info_pair(self) -> "dict[str, str]":
        """
        {new dist-info path: old dist-info path} of the two versions.

        The folder of a .dist-info carries the version in its name
        (pyjwt-2.14.0.dist-info -> pyjwt-2.15.1.dist-info), so the same file of
        two versions has two paths and the path comparison alone makes every
        file of the folder an A + D pair: the whole METADATA, the RECORD and
        the licenses are shipped again on every version bump, the biggest part
        of the update pack of a real library. The files below the folder are
        matched by the path under it instead: the file of the new version
        becomes M (its content changed, the patch source is the old file) or C
        (the content is the same, e.g. a license), and only the folder name
        moves. The files of the old folder keep their D records, the folder
        must not stay on the disk.

        The folders are paired only when they belong to the same distribution
        (the name before the version is the same): the folder of another
        distribution is not the old version of this one, its files keep the
        A + D records of unrelated paths.

        Returns:
            dict[str, str]: {path in the new version: path in the old version}
        """
        old_folder = self.old.dist_info
        new_folder = self.new.dist_info
        if old_folder == new_folder:
            return {}
        # the version of the folder is behind the name, the '-info' of the
        # suffix is not a separator: 'demo-1.0.dist-info' -> 'demo'
        old_package = removesuffix(old_folder, '.dist-info')
        new_package = removesuffix(new_folder, '.dist-info')
        if old_package.rpartition('-')[0] != new_package.rpartition('-')[0]:
            return {}
        old_prefix = f'{old_folder}/'
        new_prefix = f'{new_folder}/'
        out = {}
        for path in self.new.tree:
            if not path.startswith(new_prefix):
                continue
            old_path = f'{old_prefix}{path[len(new_prefix):]}'
            if old_path in self.old.tree:
                out[path] = old_path
        return out

    @cached_property
    def diff_info(self) -> "dict[str, UpdateInfo]":
        """
        File changes from the old version to the new version, keyed by path.

        source_path points to the old file the record references: the old
        file of the same path for an M record with patch data, the copied
        file for a C record. It is empty for an A / D record and for an M
        record with plain data.

        A record whose content an unchanged old file or an earlier record of
        the new version already carries is decided to be a C (copied) record
        before the encoding, so no candidate of it is ever compressed (see
        _try_copy).

        Returns:
            dict[str, UpdateInfo]: {path: UpdateInfo}
        """
        old_files = self.old.tree
        new_files = self.new.tree
        unchanged = {
            path for path in old_files.keys() & new_files.keys()
            if self._is_unchanged(old_files[path], new_files[path])
        }
        # {content sha1: source path} for the copy detection, only the
        # content matters. An unchanged old file is the source of the copies
        # of the new version (the file stays, a copy references it through
        # the refinfo), the changed files are added as they are built
        source_map = {}
        for path, file in old_files.items():
            if path in unchanged:
                source_map.setdefault(file.sha1, path)

        out = {}
        pair = self.dist_info_pair
        for path, new_file in new_files.items():
            if path in unchanged:
                continue
            # the file of the old version this record comes from: the same
            # path, or the same path under the .dist-info folder of the old
            # version, see dist_info_pair
            old_path = path if path in old_files else pair.get(path, '')
            old_file = old_files.get(old_path) if old_path else None
            record = UpdateInfo(
                path=path, edit=1 if old_file is not None else 0, eol=2, mode=new_file.mode)
            # a content that already exists (an unchanged old file, or an
            # earlier record of the new version) makes the record a C (copied)
            # record, and a copy carries no data at all: the check runs before
            # the encoding, so the content of a copy is never compressed. An
            # empty file is never a copy, only a non-empty content is shared
            # (the same rule the full pack and the git diff keep)
            content_sha1 = new_file.sha1 if new_file.content else b''
            copied = self._try_copy(record, source_map, content_sha1)
            if not copied and content_sha1 and old_file is not None and old_file.sha1 == content_sha1:
                # the content is the same and only the path moved (the folder
                # of a .dist-info carries the version): C from the old file
                record.edit = 0
                record.source_path = old_path
                copied = True
            if copied:
                record.size = len(new_file.content)
                record.sha1 = content_sha1
            elif old_file is None:
                # A (added)
                self._load_added(record, new_file)
            elif self._load_modified(record, old_file, new_file):
                # M (modified), the record references the old file when the
                # zstd patch won the encoding
                record.source_path = old_path
            if content_sha1:
                source_map[content_sha1] = path
            out[path] = record

        # 2. deleted: the files the new version does not have, in the DFS
        # path order of the old version, like RepoDiff emits them
        for path in old_files:
            if path not in new_files:
                out[path] = self._new_deleted(path)
        return out

    @cached_property
    def refinfo(self) -> "dict[str, RefInfo]":
        """
        Old file records referenced by the diff records.

        These records must appear in the refinfo of the update pack: the
        sources of the M (patch) records and the unchanged old files the C
        records copy. A copied record whose source is a new file (an earlier
        record of the new version) is not a ref record.

        The order is the DFS path sort of the pack, the convention shared
        with the client's local old index (see PackUpdate.refinfo).

        Returns:
            dict[str, RefInfo]: {filepath: RefInfo}
        """
        diff = self.diff_info
        unchanged = set(self.old.tree) & set(self.new.tree) - set(diff)
        # the sources of the C records: an unchanged old file, or the file of
        # the old version under the .dist-info folder that moved
        moved = set(self.dist_info_pair.values())
        ref_paths = set()
        for info in diff.values():
            if not info.source_path:
                continue
            if info.edit == 1:
                # an M record only references the old file when patch data is
                # used, source_path is empty otherwise
                ref_paths.add(info.source_path)
            elif info.source_path in unchanged or info.source_path in moved:
                # copied from an unchanged old file, or from the file of the
                # old version under the folder that moved (dist-info)
                ref_paths.add(info.source_path)
        out = {}
        for path in sorted(ref_paths, key=_dfs_path_key):
            file = self.old.tree[path]
            out[path] = RefInfo(path=path, size=len(file.content), sha1=file.sha1)
        return out

    @staticmethod
    def _is_unchanged(old_file, new_file):
        """
        Check whether a file is identical in both versions.

        Args:
            old_file (WheelFile): Old file
            new_file (WheelFile): New file

        Returns:
            bool: True if the content and the mode are the same, the file
                can be left out of the diff
        """
        return old_file.sha1 == new_file.sha1 and old_file.mode == new_file.mode

    def _load_added(self, info, new_file):
        """
        Load the data of an added file, the best of raw / lzma / zstd.

        The encoding only depends on the content, so it is cached by the
        content sha1: every version of a run that carries the content takes
        the bytes from the cache instead of compressing again. See
        PackCache.content_update.

        Args:
            info (UpdateInfo): Record to load, edit must be A (or M without
                a patch source, the caller converts it)
            new_file (WheelFile): New file
        """
        cache = self.cache
        key = new_file.sha1.hex()
        cached = cache.get(cache.content_update, key)
        if cached is not None:
            apply_encoding(info, cached)
            return
        PackFull._load_data(info, new_file.content, cache_info=self.plain_entry(new_file))
        cache.content_update[key] = FileInfo(
            path=info.path, algo=info.algo, size=info.size,
            data_size=info.data_size, sha1=info.sha1, data=info.data)
        cache.content_update.miss += 1

    def _load_modified(self, info, old_file, new_file):
        """
        Load the data of a modified file, a zstd patch from the old file.

        The best of raw / lzma / zstd patch-from / plain zstd is stored, the
        patch-from wins for similar contents, the expected case of an M
        record. The encoding only depends on the content pair (old, new), so
        it is cached by the pair: a version that shares the pair with an
        earlier one takes the bytes from the cache. See PackCache.patch.

        Args:
            info (UpdateInfo): Record to load, edit must be M
            old_file (WheelFile): Old file of the same path, the patch source
            new_file (WheelFile): New file

        Returns:
            bool: True if the zstd patch-from data was stored, the old file
                is then referenced by the record
        """
        if not old_file.content:
            # an empty old file has no content to use as the zstd dictionary,
            # the encoding is the one of an added file
            self._load_added(info, new_file)
            return False
        cache = self.cache
        key = (old_file.sha1, new_file.sha1)
        cached = cache.get(cache.patch, key)
        if cached is not None:
            apply_encoding(info, cached.info)
            return cached.patch_used
        algo_name = PackFull._load_data(
            info, new_file.content,
            cache_info=self.plain_entry(new_file), zstd_source=old_file.content)
        patch_used = algo_name == 'zstd_patch'
        cache.patch[key] = PatchCache(FileInfo(
            path=info.path, algo=info.algo, size=info.size,
            data_size=info.data_size, sha1=info.sha1, data=info.data), patch_used)
        cache.patch.miss += 1
        return patch_used

    def plain_entry(self, new_file):
        """
        The cache entry of the content of a new file: its plain encodings.

        Both plain inputs of the encoding live on the entry (see PlainCache):
        the info slot is the raw / lzma encoding of the content (the bar the
        patch is measured against, stored by the build of the full pack or
        by an earlier record), the zstd slot is left on it by the first
        record that compares the plain zstd candidate. An entry of another
        size is not the content and is ignored, the caller compresses from
        scratch then. The lookup takes no lock, see PackCache.get.

        Args:
            new_file (WheelFile): New file of a record

        Returns:
            PlainCache: The cache entry of the content, a fresh one without
                encodings when the cache has no entry to serve the record
        """
        cache = self.cache
        entry = cache.get(cache.content_index, new_file.sha1.hex())
        if entry is None or entry.info is None or entry.info.size != len(new_file.content):
            return PlainCache()
        return entry

    @staticmethod
    def _try_copy(info, source_map, sha1):
        """
        Convert a record to a copied record when its content already exists.

        A record whose content matches an unchanged old file (kept in the new
        version) or an earlier record references the source instead of
        carrying data. Only the content matters: the converted record keeps
        its own eol / mode, encoded in the pack. The caller checks this before
        the encoding of the record, so the content of a copy is never
        compressed (see diff_info).

        Args:
            info (UpdateInfo): Record to convert
            source_map (dict[str, str]): {content sha1: source path}
            sha1 (bytes): Content sha1 of the record, b'' for an empty content

        Returns:
            bool: True when the record is a copy now, it carries no data
        """
        if not sha1:
            # empty files are not considered as copies
            return False
        source_path = source_map.get(sha1)
        if source_path is None:
            return False
        info.edit = 0
        info.source_path = source_path
        return True

    @staticmethod
    def _new_deleted(path):
        """
        Create an empty record to indicate a file that should not exist.

        Args:
            path (str): Path of the file of the old version

        Returns:
            UpdateInfo: The D (deleted) record
        """
        return UpdateInfo(path=path, edit=2)


class PackWheelUpdate(PackUpdate):
    """
    An update pack that upgrades the install tree of an old wheel to a new one.

    The class is the wheel sibling of PackUpdate and inherits the assembly of
    the records from it: diff_info merges the diff of the two install trees
    (WheelDiff) with the record of the index pack (the ledger), fileinfo
    resolves the source_lookback of every record, and refinfo collects the
    old files the records reference. Only the inputs of the diff and the
    encoding of the index record are wheel specific, see the module
    docstring.

    Attributes:
        new (PackWheel): New version
        old (PackWheel): Old version
        cache (PackCache): Cache of the pack, the one of the new version
    """

    def __init__(self, new, old):
        """
        Args:
            new (PackWheel): New version, the install tree the update
                upgrades to
            old (PackWheel): Old version, the install tree the update
                upgrades from. An already published old pack must be rebuilt
                with the pack format version it was encoded with
                (PackWheel(old_wheel, pack_version=...)): its index pack must
                be the ledger the clients hold, a pack of another format
                gives another index and the clients download the new index
                instead of patching it (the file records are not affected,
                the format changes the encoding, not the content)

        Raises:
            ValueError: If new or old is not a PackWheel
        """
        # the git constructor of PackUpdate is skipped: the sides are wheels,
        # not a git repo and a commit
        PackEncodeBase.__init__(self)
        if not isinstance(new, PackWheel):
            raise ValueError(
                f'PackWheelUpdate requires a PackWheel of the new version, got {type(new).__name__}')
        if not isinstance(old, PackWheel):
            raise ValueError(
                f'PackWheelUpdate requires a PackWheel of the old version, got {type(old).__name__}')
        self.new = new
        self.old = old
        # the records of the pack are validated and encoded through the cache of
        # the new version, like its diff (WheelDiff): the process cache bound by
        # PackEncodeBase.__init__ is the one of the git pipeline
        self.cache = new.cache
        # the update pack updates from the old version to the current one,
        # and is encoded in the format of the new pack, like PackUpdate
        self.pack_version = new.pack_version
        self.current_version = new.current_version
        self.old_version = old.current_version
        self._diff = WheelDiff(old, new)

    def _index_pack_diff(self):
        """
        Build the diff record of the index pack, see PackUpdate._index_pack_diff.

        The new index pack bytes are the content, compressed with the old
        index pack as the zstd patch source; the plain slot of the comparison
        is filled by encode_content, through the cache of the new version.
        When the patch wins, the record references the old index like any
        other M record and the client verifies its local ledger against the
        refinfo. When the two versions share the same index pack, no record
        is produced.

        Returns:
            UpdateInfo | None: The M record of the index pack, or None when
                the index pack did not change
        """
        old_index = self.old.index_pack
        new_index = self.new.index_pack
        if old_index == new_index:
            return None
        info = UpdateInfo(path='.pack/index.pack', edit=1, eol=2, mode=0)
        cache_info = self.new.encode_content(info, new_index)
        algo_name = PackFull._load_data(
            info, new_index, cache_info=cache_info, zstd_source=old_index)
        if algo_name == 'zstd_patch':
            # the patch needs the old index as the dictionary
            info.source_path = '.pack/index.pack'
        return info
