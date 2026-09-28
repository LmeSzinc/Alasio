"""
In-process cache of the pack server pipeline.

The pack server builds one full pack of the latest version and one update pack
from every lookback version to the latest one. Both sides encode the same
content again and again, the cache removes the repeated work:

- a content is encoded once per rule set, keyed by the **git blob sha1** (the
  hex str the git tree carries) of the
  file: rebuilding any version that shares the content skips the blob read and
  the compression. Two encodings of one content are kept apart because both
  must keep their exact bytes: the index encoding (raw / lzma, what the index
  pack of a version stores, it must stay byte-identical to the index pack the
  clients hold) and the update encoding (raw / lzma / zstd, what an A record of
  an update pack carries)
- an encoded content pair (old content sha1, new content sha1) is an M / RM
  patch: a patch depends on the old content as the zstd dictionary and on
  nothing else, so versions that share the pair share the patch, renames
  included

The cache belongs to the caller: the pipeline creates one PackCache per repo run
and passes it to every PackFull / PackUpdate, so the tables live across the
versions. It is not persisted, every entry is a pure function of the repo
content, a later run recomputes what it needs.

- the resolved .gitattributes attributes of a path (what the eol of its record
  is decided from) are kept per .gitattributes state, and the eol of a
  text="auto" path is kept per (path, content): the .gitattributes files of a
  repo rarely change and a content rarely changes twice, so the versions of a
  run share the resolutions and a version resolves only the paths and the
  contents its predecessors did not see, see PackFull._populate_eol

Measured on AzurLaneAutoScript (see doc/2026-09-27_update-pack-from-repo.md):
200 lookback versions need 1,522 unique A record encodings and 1,842 unique
patches against 222,218 and 32.9 times that many record occurrences, the cache
is worth about 29 minutes per run for some 26MB.
"""


class ContentCache:
    """
    The two encodings of one content, filled lazily by the pack that needs them.

    Attributes:
        index (FileInfo | None): Encoding with the full pack rules
            (PackFull._populate_data: raw / lzma), None until a version needs
            it. The index pack of a version stores algo / size / data_size, the
            bytes must stay byte-identical to the published pack
        update (FileInfo | None): Encoding with the update pack rules
            (RepoDiff._load_added: raw / lzma / zstd), None until an A record
            needs it
    """

    __slots__ = ('index', 'update')

    def __init__(self):
        self.index = None
        self.update = None


class PatchCache:
    """
    The M / RM encoding of one content pair.

    Attributes:
        info (FileInfo): Encoding with the update pack rules
            (RepoDiff._load_modified: raw / lzma / zstd patch-from / zstd)
        patch_used (bool): True when info.data is the zstd patch from the old
            content, the record then references the old file and the client
            decompresses with it
    """

    __slots__ = ('info', 'patch_used')

    def __init__(self, info, patch_used):
        self.info = info
        self.patch_used = patch_used


class PackCache:
    """
    Shared cache of the encoded content and the encoded patches.

    Attributes:
        content (dict[str, ContentCache]): {git blob sha1 hex: ContentCache}
        patch (dict[tuple, PatchCache]): {(old content sha1, new content sha1):
            PatchCache}
        extra (dict[tuple, FileInfo]): {(version, filepath): FileInfo} of the
            generated extra files (the index pack, the commit history), they
            are not files of the repo and have no git blob sha1, their raw /
            lzma encoding is a pure function of (version, filepath)
        rename (dict[tuple, int]): {(deleted git blob sha1 hex, added git blob
            sha1 hex): zstd patch length} of the rename scores, the score is a
            pure function of the two revisions see RepoDiff.similarity
        eol (dict[str, dict]): {gitattributes fingerprint: table} of the version
            files, one table for every .gitattributes state of the repo. A table
            has two kinds of keys:

            - {filepath: attrs_dict}: the resolved .gitattributes attributes of
              the path, the very dict that FileAttrs.attrs_dict builds, so
              PackFull._populate_eol decides the eol from the cached attributes
              without running the rule engine
            - {(filepath, git blob sha1): eol}: the eol of a text="auto" path,
              where the content decides text or binary. A tuple key can not
              clash with the path keys, an unchanged content is reused as is,
              a changed content (another blob sha1) is resolved and cached as
              another entry

            The attributes of a path change only when the .gitattributes files
            change, so the versions of a run share the tables
        stat (dict[str, list[int]]): [hit, miss] of every table

    Usage:
        cache = PackCache()
        pack = PackFull(repo, commit, cache=cache)
        update = PackUpdate(pack, old_commit, cache=cache)
        logger.info(cache.report())
    """

    # names of the tables, the keys of stat
    TABLES = ('content', 'patch', 'extra', 'rename', 'eol')

    def __init__(self):
        self.content: "dict[str, ContentCache]" = {}
        self.patch: "dict[tuple, PatchCache]" = {}
        self.extra: "dict[tuple, FileInfo]" = {}
        self.rename: "dict[tuple, int]" = {}
        self.eol: "dict[str, dict]" = {}
        # [hit, miss] of every table
        self.stat: "dict[str, list]" = {name: [0, 0] for name in self.TABLES}

    def mark(self, name, hit):
        """
        Count one lookup of a table

        Args:
            name (str): Table name, one of TABLES
            hit (bool): True when the lookup hit
        """
        stat = self.stat[name]
        stat[0 if hit else 1] += 1

    def mark_many(self, name, hit, miss):
        """
        Count a batch of lookups of a table

        The tables looked up once per file are counted in one go, see
        PackFull._populate_eol.

        Args:
            name (str): Table name, one of TABLES
            hit (int): Lookups that hit
            miss (int): Lookups that missed
        """
        stat = self.stat[name]
        stat[0] += hit
        stat[1] += miss

    def file_size(self):
        """
        Total size of the encoded data held by the cache

        Returns:
            int: Bytes of the data, without the overhead of the records
        """
        size = 0
        for entry in self.content.values():
            for info in (entry.index, entry.update):
                if info is not None:
                    size += len(info.data)
        size += sum(len(entry.info.data) for entry in self.patch.values())
        return size + sum(len(info.data) for info in self.extra.values())

    def report(self):
        """
        Render the cache usage as one log line

        Every table is rendered as ``name=hit/lookup``, the lookup count is
        hits plus misses.

        Returns:
            str: Hit rate and size of every table
        """
        rows = [
            f'{name}={self.stat[name][0]}/{sum(self.stat[name])}'
            for name in self.TABLES
        ]
        return (
            f'PackCache: {", ".join(rows)}, '
            f'entries={len(self.content)}/{len(self.patch)}/{len(self.extra)}, '
            f'data={self.file_size() / 1048576:.1f}MB'
        )
