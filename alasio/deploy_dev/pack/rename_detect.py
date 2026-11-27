"""
Rename detection: match the files a version lost with the files it gained.

Both pack pipelines compare two versions of a tree to build an update pack, and
both face the same question: the file that disappeared from the old version and
the file that appeared in the new one may be one file under another name. The
git pipeline (RepoDiff) sees it on a source tree, the dist pipeline (WheelDiff)
on the install tree of a wheel, where it matters most: a built frontend names
every file by the hash of its content, so a rebuild that changes one chunk
renames the whole import graph of the bundle, and an update pack that ships each
of those files again as an A + D pair is hundreds of times larger than the change
it describes.

The matching is a policy of the pack format, shared by the two pipelines:

- a pair with the same content sha1 is an exact rename, its similarity is 1
- the other pairs are filtered by the size ratio (RenameDetect.MAX_SIZE_RATIO)
  before anything is compressed, then scored by zstd dictionary compression
  (see RenameDetect.similarity)
- the pairs above RenameDetect.MIN_SIMILARITY are matched greedily one to one by
  descending similarity: every old file is the source of at most one rename,
  because an R / RM record moves the old file, it does not copy it

The score of a content pair is a pure function of the two revisions, so it is
cached (PackCache.rename) and shared by the versions of a run: a version pair
that matches the two revisions an earlier pair matched pays no compression. The
callers read the contents, the matcher works on records with path / size / sha1
only, so a pipeline passes its own reader (a git blob, the member of a wheel).

Usage:
    from alasio.deploy_dev.pack.rename_detect import RenameDetect

    detect = RenameDetect(cache, read_old, read_new)
    renames = detect.find(deleted, added)
"""

from alasio.ext.compress.algo_zstd import zstd_compress


class RenameDetect:
    """
    Match deleted files with added files by content similarity, see the module
    docstring.

    Attributes:
        cache (PackCache): Cache of the encoder, the score of a content pair is
            kept in cache.rename, see score
        read_old (Callable): read_old(record) -> bytes, content of a deleted
            file of a candidate
        read_new (Callable): read_new(record) -> bytes, content of an added
            file of a candidate
        min_similarity (float): Minimum similarity of a pair to be matched,
            0~1
        max_size_ratio (float): Maximum size ratio of a candidate pair, a pair
            outside [1 / ratio, ratio] is never matched
        similarity_level (int): Zstd level of the similarity score
    """

    # Minimum similarity of a rename pair, 0~1, like git's default 50%
    # rename threshold.
    MIN_SIMILARITY = 0.5

    # Maximum size ratio of rename candidates, pairs outside
    # [1 / ratio, ratio] are never matched.
    MAX_SIZE_RATIO = 4.0

    # Zstd level of the rename similarity score, a fast level is enough.
    SIMILARITY_LEVEL = 3

    def __init__(
            self,
            cache,
            read_old,
            read_new,
            min_similarity=MIN_SIMILARITY,
            max_size_ratio=MAX_SIZE_RATIO,
            similarity_level=SIMILARITY_LEVEL,
    ):
        """
        Args:
            cache (PackCache): Cache of the encoder, the score of a content pair
                is kept in cache.rename, see _pack_cache
            read_old (Callable): read_old(record) -> bytes, content of a
                deleted file of a candidate
            read_new (Callable): read_new(record) -> bytes, content of an added
                file of a candidate
            min_similarity (float): Minimum similarity of a pair to be matched,
                0~1. Defaults to MIN_SIMILARITY.
            max_size_ratio (float): Maximum size ratio of a candidate pair.
                Defaults to MAX_SIZE_RATIO.
            similarity_level (int): Zstd level of the similarity score.
                Defaults to SIMILARITY_LEVEL.
        """
        self.cache = cache
        self.read_old = read_old
        self.read_new = read_new
        self.min_similarity = min_similarity
        self.max_size_ratio = max_size_ratio
        self.similarity_level = similarity_level

    def find(self, deleted, added):
        """
        Match the deleted files with the added files, one to one.

        Pairs with the same content sha1 are exact renames, their similarity is
        1. The other pairs are filtered by the size ratio and scored by zstd
        dictionary compression (see score). The candidates above
        min_similarity are matched greedily one to one by descending
        similarity: every old file is the source of at most one rename, and the
        tie of two pairs with the same score is broken by the paths, so the
        matching is deterministic.

        Args:
            deleted (list): Records of the old files the new version does not
                have, each one with the path / size / sha1 attributes
            added (list): Records of the new files the old version does not
                have

        Returns:
            dict[str, str]: {new path: old path} of the matched renames
        """
        candidates = []
        for new in added:
            for old in deleted:
                # an empty file is not a content to match, it is cheaper as an
                # A + D pair than as a rename with a source record
                if not old.size or not new.size:
                    continue
                if old.sha1 == new.sha1:
                    # exact content match, similarity is 1
                    sim = 1.0
                else:
                    # size pre-filter before compressing the pair
                    ratio = new.size / old.size
                    if not (1 / self.max_size_ratio <= ratio <= self.max_size_ratio):
                        continue
                    sim = self.score(old, new)
                if sim >= self.min_similarity:
                    candidates.append((sim, new.path, old.path))

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

    def score(self, old, new):
        """
        Similarity of a candidate pair, cached by the content pair

        The score is a pure function of the two revisions, so the zstd patch
        length it is derived from is stored in the cache, keyed by the content
        sha1 pair of the deleted and the added revision: every version of the
        window that pairs the same two revisions takes the score instead of
        compressing again, and a hit needs no content read at all, the sizes
        come from the records. See PackCache.rename.

        Args:
            old (object): Record of the deleted file, path / size / sha1
            new (object): Record of the added file

        Returns:
            float: Similarity in [0, 1], see similarity
        """
        cache = self.cache
        key = (old.sha1, new.sha1)
        length = cache.get(cache.rename, key)
        if length is None:
            length = len(zstd_compress(
                self.read_new(new), source=self.read_old(old),
                level=self.similarity_level))
            cache.rename[key] = length
            cache.rename.miss += 1
        return 1 - length / new.size

    @staticmethod
    def similarity(old_content, new_content, level=SIMILARITY_LEVEL):
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
                to SIMILARITY_LEVEL (3), a fast level is enough for a score.

        Returns:
            float: Similarity in [0, 1], higher is more similar
        """
        patch = zstd_compress(new_content, source=old_content, level=level)
        return 1 - len(patch) / len(new_content)
