/*
 * Path combination encoder: the filepath section of a pack index in one
 * pass, plus the two combined encoders of pathlen_coding.py.
 *
 * The section is three parallel arrays, all of them written here:
 *
 *   prefix_comb   prefix reuse (diff + zigzag) of every path combined with
 *                 the byte length of its remaining path, one int per path,
 *                 read back by decode_prefix_comb()
 *   suffix_comb   suffix reuse of every path combined with the lookback
 *                 distance of the path it reuses, one int per path, read
 *                 back by decode_suffix_comb()
 *   remaining     the remaining path bytes, concatenated in order, the
 *                 decoder takes path_len bytes per entry
 *
 * The suffix search indexes the paths already seen in a trie of the bytes
 * of their tails, read from the end of the path backwards:
 *
 *   A path stops at the node reached by the last `suffix + last + second`
 *   bytes: the file suffix (from the last dot of the basename, the dot
 *   included), the last character of the stem and the one before it. That
 *   is exactly the key of the reference PathLookbackLCS, and the paths that
 *   stop at a node are exactly the entries of its group, in insertion
 *   order. Level 1 walks them, newest first, and stops at the lookback
 *   limit.
 *
 *   The node of the (suffix, last) classes, the level 2 candidates, is the
 *   ancestor of the stop node at that depth, and its class list holds every
 *   class of the same (suffix, last) in creation order: level 2 reads the
 *   newest entry of a handful of nodes instead of walking the groups of the
 *   whole suffix (measured on a real repository: 3.9 classes instead of
 *   54.7 groups).
 *
 *   The suffix buckets of level 3 keep their own small list: that level
 *   scores the suffix of the query against every suffix seen, a comparison
 *   the trie does not help with, and it runs for one path in twenty.
 *
 * A node finds its child with a 256 bit map of the byte values it holds and
 * the rank of the query byte in that map: one word of the map and one
 * popcount, no hashing, and the children of a node are one array. The
 * members of a node are one block too, filled before the search runs, so
 * the level 1 walk walks consecutive memory instead of one chain per path.
 *
 * The search reads back with _decode_paths() of the pack decoder, so its
 * only contract is the bytes it emits, and the Python reference is
 * iter_path_comb() plus encode_prefix_comb() and encode_suffix_comb() of
 * alasio/ext/algorithm/pathlen_coding.py: this encoder emits exactly what
 * they emit, byte for byte, tests_speedup/test_pathcomb.py compares the two
 * on every branch of the search, on the paths of a real repository
 * included.
 *
 * The parameters below are the pack format, not a knob of the call: they
 * mirror the constants of pathlen_coding.py and the defaults of
 * pathcomb.py, and changing one of them changes the bytes, so it is a new
 * pack version and a new ABI version, never an argument.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define PATHCOMB_EXPORT __declspec(dllexport)
#else
#define PATHCOMB_EXPORT __attribute__((visibility("default")))
#endif

/* interface version of this library, raised with every change of the
   signature or of the frozen parameters below */
#define PATHCOMB_ABI_VERSION 2

/*
 * Frozen parameters, mirrors of alasio/ext/algorithm/pathlen_coding.py
 * (MAX_PREFIX_REUSE, MAX_PATH_LEN, MAX_SUFFIX_REUSE,
 * MAX_SUFFIX_LOOKBACK) and of the min_suffix_reuse default of
 * alasio/ext/algorithm/pathcomb.py (MIN_SUFFIX_REUSE). They are verified
 * against the Python constants by pathcomb_encode_c.py, which refuses the
 * accelerator when they differ, and by the test suite.
 */
#define PATHCOMB_MAX_PREFIX_REUSE 32639
#define PATHCOMB_MIN_SUFFIX_REUSE 3
#define PATHCOMB_MAX_SUFFIX_REUSE 65535
#define PATHCOMB_MAX_PATH_LEN 65535
#define PATHCOMB_MAX_SUFFIX_LOOKBACK 255

/* biases of the combined encoding, see encode_prefix_comb() */
#define PATHCOMB_BIAS_1B1B 256
#define PATHCOMB_BIAS_2B2B 16777216

/* a pack index never comes near this, the guard only keeps the arrays of
   one run out of an overflow */
#define PATHCOMB_MAX_PATHS 16777216

/* errors, the return value of the path encoder is the byte count written */
#define PATHCOMB_ERR_ARGUMENT (-1)
#define PATHCOMB_ERR_RANGE (-2)
#define PATHCOMB_ERR_CAPACITY (-3)

/* a byte range of the input: a path, a suffix or one character */
typedef struct {
    const char *ptr;
    uint32_t len;
} PathCombSlice;

/* one node of the trie of the path tails, one byte deep */
typedef struct {
    uint32_t parent;
    uint32_t depth; /* bytes from the root */
    uint32_t first_child; /* index into the child pool, 1 based, 0 = none */
    uint32_t child_count;
    uint32_t child_cap;
    uint32_t bitmap[8]; /* bit b set: the byte b has a child */
    uint32_t stop_first; /* first member slot of this node */
    uint32_t stop_count; /* members counted by the layout pass */
    uint32_t filled; /* members inserted by the search pass so far */
    uint32_t linked; /* the node is in the class list and the bucket */
    uint32_t l2_next; /* the next class of the same (suffix, last) */
    uint32_t class_first; /* the classes of this (suffix, last), head */
    uint32_t class_tail;
    uint32_t bucket; /* the suffix bucket this node belongs to */
    uint32_t bucket_next;
} PathCombNode;

/* one path of a group, in insertion order */
typedef struct {
    uint32_t path_offset;
    uint32_t path_len;
    uint32_t index; /* the current index of the path in the list */
} PathCombMember;

/* one suffix bucket of the level 3 search, in creation order */
typedef struct {
    PathCombSlice suffix;
    uint32_t first_group;
    uint32_t tail_group;
    uint32_t group_count;
    uint32_t entries; /* groups holding at least one entry so far */
} PathCombBucket;

/* the state of one encode run */
typedef struct {
    const char *paths; /* NUL separated paths, the input blob */
    int32_t n; /* number of paths */
    PathCombNode *nodes;
    uint32_t node_count;
    uint32_t node_cap;
    uint32_t *children; /* the child pool of every node */
    uint32_t child_used;
    uint32_t child_cap;
    PathCombMember *members;
    uint32_t member_count;
    uint32_t member_cap;
    uint32_t *slot; /* per path: the slot its entry lives in */
    uint32_t *first_of; /* per path: the first path with the same value */
    uint32_t *path_offset; /* per path: its offset in the blob */
    uint32_t *path_len; /* per path: its byte length */
    uint32_t *stop_node; /* per path: the node its key stops at */
    uint32_t *key_chars; /* per path: the characters of its key */
    PathCombBucket *buckets;
    uint32_t bucket_count;
    uint32_t bucket_cap;
} PathComb;

/*
 * Compare two paths of the run by their bytes, the order of the sort that
 * finds the paths seen twice: the shorter one first when one is a prefix
 * of the other, any total order works, only that equal paths end up next
 * to each other.
 */
static int pathcomb_path_before(const PathComb *comb, uint32_t a, uint32_t b)
{
    uint32_t a_len = comb->path_len[a];
    uint32_t b_len = comb->path_len[b];
    uint32_t min = a_len < b_len ? a_len : b_len;
    int order = min ? memcmp(comb->paths + comb->path_offset[a], comb->paths + comb->path_offset[b], min) : 0;
    if (order != 0) {
        return order < 0;
    }
    return a_len < b_len;
}

/*
 * Sort the path indices of one range by their bytes, a merge sort: the sort
 * has to compare through the run state, which rules out qsort().
 */
static void pathcomb_sort(PathComb *comb, uint32_t *values, uint32_t *buffer, uint32_t count)
{
    uint32_t width;
    for (width = 1; width < count; width *= 2) {
        uint32_t start;
        for (start = 0; start < count; start += 2 * width) {
            uint32_t mid = start + width < count ? start + width : count;
            uint32_t end = start + 2 * width < count ? start + 2 * width : count;
            uint32_t i = start;
            uint32_t j = mid;
            uint32_t k = start;
            while (i < mid && j < end) {
                buffer[k++] = pathcomb_path_before(comb, values[j], values[i])
                        ? values[j++] : values[i++];
            }
            while (i < mid) {
                buffer[k++] = values[i++];
            }
            while (j < end) {
                buffer[k++] = values[j++];
            }
        }
        memcpy(values, buffer, (size_t)count * sizeof(uint32_t));
    }
}

/*
 * Mark, for every path, the first path of the run with the same value: the
 * reference keys its groups by the value of the path, so a path seen twice
 * refreshes the index of the entry of its first occurrence instead of
 * taking a second one.
 */
static int pathcomb_mark_first(PathComb *comb)
{
    uint32_t count = (uint32_t)comb->n;
    uint32_t *order = (uint32_t *)malloc((size_t)count * sizeof(uint32_t));
    uint32_t *buffer = (uint32_t *)malloc((size_t)count * sizeof(uint32_t));
    uint32_t i;
    if (order == NULL || buffer == NULL) {
        free(order);
        free(buffer);
        return 1;
    }
    for (i = 0; i < count; i++) {
        order[i] = i;
        comb->first_of[i] = i;
    }
    pathcomb_sort(comb, order, buffer, count);
    for (i = 1; i < count; i++) {
        uint32_t previous = order[i - 1];
        uint32_t current = order[i];
        if (comb->path_len[previous] == comb->path_len[current]
                && (comb->path_len[current] == 0
                    || memcmp(comb->paths + comb->path_offset[previous],
                              comb->paths + comb->path_offset[current], comb->path_len[current]) == 0)) {
            comb->first_of[current] = comb->first_of[previous];
        }
    }
    free(order);
    free(buffer);
    return 0;
}

/* ── UTF-8, the paths are character sequences, the lengths are too ────── */

/*
 * The byte length of the character starting at data, clamped to the
 * remaining bytes: the input is the UTF-8 of a Python str, always well
 * formed, the clamp only keeps an ill formed input from running away.
 */
static uint32_t pathcomb_char_len(const char *data, uint32_t remaining)
{
    uint8_t head = (uint8_t)data[0];
    uint32_t len = 1;
    if (head >= 0xf0u) {
        len = 4;
    } else if (head >= 0xe0u) {
        len = 3;
    } else if (head >= 0xc0u) {
        len = 2;
    }
    return len < remaining ? len : remaining;
}

/* the population count of 32 bits, without a builtin */
static uint32_t pathcomb_popcount(uint32_t value)
{
    value = value - ((value >> 1) & 0x55555555u);
    value = (value & 0x33333333u) + ((value >> 2) & 0x33333333u);
    value = (value + (value >> 4)) & 0x0f0f0f0fu;
    return (value * 0x01010101u) >> 24;
}

/*
 * The character count of a UTF-8 range, eight bytes at a time: a byte that
 * is not a continuation byte starts exactly one character, which is what
 * len() counts.
 */
static uint32_t pathcomb_strlen(const char *data, uint32_t len)
{
    uint32_t count = 0;
    uint32_t i = 0;
    uint64_t mask;
    while (i + 8 <= len) {
        memcpy(&mask, data + i, 8);
        /* a continuation byte has the high bit set and the second bit
           clear, the shifted second bits select them */
        mask = (mask & 0x8080808080808080ull) & ~((mask & 0x4040404040404040ull) << 1);
        mask = mask - ((mask >> 1) & 0x5555555555555555ull);
        mask = (mask & 0x3333333333333333ull) + ((mask >> 2) & 0x3333333333333333ull);
        mask = (mask + (mask >> 4)) & 0x0f0f0f0f0f0f0f0full;
        count += 8 - (uint32_t)((mask * 0x0101010101010101ull) >> 56);
        i += 8;
    }
    while (i < len) {
        if (((uint8_t)data[i] & 0xc0u) != 0x80u) {
            count++;
        }
        i++;
    }
    return count;
}

/*
 * The character count of the longest common prefix of two ranges, the
 * len() of get_lcp() of the reference, and the byte length of the
 * characters it counts.
 */
static uint32_t pathcomb_lcp(
        const char *a, uint32_t a_len, const char *b, uint32_t b_len, uint32_t *out_bytes)
{
    uint32_t max = a_len < b_len ? a_len : b_len;
    uint32_t k = 0;
    uint64_t x, y;
    while (k + 8 <= max) {
        memcpy(&x, a + k, 8);
        memcpy(&y, b + k, 8);
        if (x != y) {
            break;
        }
        k += 8;
    }
    while (k < max && a[k] == b[k]) {
        k++;
    }
    if (k < max) {
        /* the common bytes may cut a character in half, that one is not
           common: back up to the boundary the count starts at */
        while (k > 0 && ((uint8_t)a[k] & 0xc0u) == 0x80u) {
            k--;
        }
    }
    *out_bytes = k;
    return pathcomb_strlen(a, k);
}

/*
 * The character count of the longest common suffix of two ranges, the
 * len() of get_lcs_length() of the reference, and the byte length of the
 * characters it counts.
 *
 * from_bytes and from_chars describe a known common suffix of the two
 * ranges, the key of the group the other range belongs to: the comparison
 * starts there and the characters above it add to from_chars.
 */
static uint32_t pathcomb_lcs(
        const char *a, uint32_t a_len, const char *b, uint32_t b_len,
        uint32_t from_bytes, uint32_t from_chars, uint32_t *out_bytes)
{
    uint32_t max = a_len < b_len ? a_len : b_len;
    uint32_t k = from_bytes;
    uint64_t x, y;

    if (k >= max) {
        /* the shorter range is the shared key alone, nothing is longer */
        *out_bytes = max;
        return pathcomb_strlen(a + (a_len - max), max);
    }
    if (a[a_len - k - 1] != b[b_len - k - 1]) {
        *out_bytes = k;
        return from_chars;
    }
    while (k + 8 <= max) {
        memcpy(&x, a + a_len - k - 8, 8);
        memcpy(&y, b + b_len - k - 8, 8);
        if (x != y) {
            break;
        }
        k += 8;
    }
    while (k < max && a[a_len - 1 - k] == b[b_len - 1 - k]) {
        k++;
    }
    if (k < max) {
        while (k > 0 && ((uint8_t)a[a_len - k] & 0xc0u) == 0x80u) {
            k--;
        }
    }
    *out_bytes = k;
    return pathcomb_strlen(a + (a_len - k), k);
}

/*
 * The byte offset of the position after count characters of the string.
 */
static uint32_t pathcomb_advance(const char *data, uint32_t len, uint32_t count)
{
    uint32_t offset = 0;
    while (count > 0 && offset < len) {
        offset += pathcomb_char_len(data + offset, len - offset);
        count--;
    }
    return offset;
}

/*
 * The key of a path, the last three parts the suffix search indexes it by:
 * the file suffix (from the last dot of the basename, the dot included, an
 * empty suffix for a basename without a dot), the last character of the
 * stem and the character before it (both empty when the stem is too
 * short).
 */
static void pathcomb_key(
        const char *path, uint32_t len,
        PathCombSlice *suffix, PathCombSlice *last, PathCombSlice *second)
{
    const char *base = path;
    const char *stem;
    uint32_t base_len = len;
    uint32_t stem_len;
    uint32_t i;

    /* the basename, after the last '/' of the path */
    for (i = len; i > 0; i--) {
        if (path[i - 1] == '/') {
            base = path + i;
            base_len = len - i;
            break;
        }
    }

    /* the last '.' of the basename cuts the extension off, the suffix
       keeps the dot; a basename without a dot is all stem */
    stem = base;
    stem_len = base_len;
    suffix->ptr = base + base_len;
    suffix->len = 0;
    for (i = base_len; i > 0; i--) {
        if (base[i - 1] == '.') {
            stem_len = i - 1;
            suffix->ptr = base + i - 1;
            suffix->len = base_len - (i - 1);
            break;
        }
    }

    /* the last two characters of the stem, characters not bytes */
    last->ptr = stem + stem_len;
    last->len = 0;
    second->ptr = stem + stem_len;
    second->len = 0;
    if (stem_len == 0) {
        return;
    }
    i = stem_len;
    while (i > 0 && ((uint8_t)stem[i - 1] & 0xc0u) == 0x80u) {
        i--;
    }
    /* stem[i - 1] begins the last character */
    last->ptr = stem + i - 1;
    last->len = stem_len - (i - 1);
    if (i > 1) {
        uint32_t end = i - 1;
        uint32_t start = end;
        while (start > 0 && ((uint8_t)stem[start - 1] & 0xc0u) == 0x80u) {
            start--;
        }
        second->ptr = stem + start - 1;
        second->len = end - (start - 1);
    }
}

/* ── the trie of the path tails ───────────────────────────────────────── */

/*
 * The rank of one byte among the children of a node: the bytes below it in
 * the 256 bit map, which is also the position of its child in the child
 * array of the node.
 */
static uint32_t pathcomb_rank(const uint32_t *bitmap, uint32_t byte)
{
    uint32_t word = byte >> 5;
    uint32_t bit = 1u << (byte & 31u);
    uint32_t rank = 0;
    uint32_t k;
    for (k = 0; k < word; k++) {
        rank += pathcomb_popcount(bitmap[k]);
    }
    rank += pathcomb_popcount(bitmap[word] & (bit - 1u));
    return rank;
}

/*
 * The child of a node for one byte, created when it is missing, the index
 * of the child node.
 *
 * The children of a node are one array in the order of their byte value,
 * which keeps the rank of a byte equal to the index of its child. The
 * pools of the nodes and of the children grow, and growing one of them
 * moves it: the node is addressed by its index through the whole call and
 * the pointers are taken again after every growth.
 */
static uint32_t pathcomb_child(PathComb *comb, uint32_t node_index, uint8_t byte)
{
    PathCombNode *node = comb->nodes + node_index;
    uint32_t word = byte >> 5;
    uint32_t bit = 1u << (byte & 31u);
    uint32_t rank = pathcomb_rank(node->bitmap, byte);
    uint32_t first;

    if (node->bitmap[word] & bit) {
        return comb->children[node->first_child + rank];
    }

    if (node->child_count == node->child_cap) {
        uint32_t cap = node->child_cap ? node->child_cap * 2 : 4;
        while (comb->child_used + cap > comb->child_cap) {
            uint32_t grow = comb->child_cap ? comb->child_cap * 2 : 1024;
            uint32_t *pool = (uint32_t *)realloc(comb->children, (size_t)grow * sizeof(uint32_t));
            if (pool == NULL) {
                return 0;
            }
            comb->children = pool;
            comb->child_cap = grow;
        }
        first = comb->child_used;
        comb->child_used += cap;
        if (node->child_cap) {
            memcpy(
                    comb->children + first, comb->children + node->first_child,
                    rank * sizeof(uint32_t));
            memcpy(
                    comb->children + first + rank + 1,
                    comb->children + node->first_child + rank,
                    (node->child_count - rank) * sizeof(uint32_t));
        }
        if (comb->node_count == comb->node_cap) {
            uint32_t nodes_cap = comb->node_cap ? comb->node_cap * 2 : 1024;
            PathCombNode *nodes = (PathCombNode *)realloc(
                    comb->nodes, (size_t)nodes_cap * sizeof(PathCombNode));
            if (nodes == NULL) {
                return 0;
            }
            comb->nodes = nodes;
            comb->node_cap = nodes_cap;
        }
        node = comb->nodes + node_index;
        node->first_child = first;
        node->child_cap = cap;
    } else {
        memmove(
                comb->children + node->first_child + rank + 1,
                comb->children + node->first_child + rank,
                (node->child_count - rank) * sizeof(uint32_t));
    }

    if (comb->node_count == comb->node_cap) {
        uint32_t nodes_cap = comb->node_cap ? comb->node_cap * 2 : 1024;
        PathCombNode *nodes = (PathCombNode *)realloc(
                comb->nodes, (size_t)nodes_cap * sizeof(PathCombNode));
        if (nodes == NULL) {
            return 0;
        }
        comb->nodes = nodes;
        comb->node_cap = nodes_cap;
        node = comb->nodes + node_index;
    }
    {
        PathCombNode *child = comb->nodes + comb->node_count;
        memset(child, 0, sizeof(*child));
        child->parent = node_index;
        child->depth = node->depth + 1;
        comb->children[node->first_child + rank] = comb->node_count;
        comb->node_count++;
        node->child_count++;
        node->bitmap[word] |= bit;
        return comb->node_count - 1;
    }
}

/*
 * The node reached by the last depth bytes of the path, created along the
 * way: the byte before the current position selects the child. The node
 * index 0 is the root.
 */
static int32_t pathcomb_traverse(PathComb *comb, const char *path, uint32_t len, uint32_t depth)
{
    uint32_t node = 0;
    uint32_t step = 0;
    if (depth > len) {
        depth = len;
    }
    while (step < depth) {
        node = pathcomb_child(comb, node, (uint8_t)path[len - 1 - step]);
        if (node == 0) {
            return -1;
        }
        step++;
    }
    return (int32_t)node;
}

/* ── the suffix buckets of the level 3 search ─────────────────────────── */

/*
 * The bucket of one suffix, created when it is missing: the suffixes are
 * few (the file extensions of the repository) and consecutive paths share
 * their extension, so the last bucket is checked first.
 */
static PathCombBucket *pathcomb_bucket(PathComb *comb, PathCombSlice suffix)
{
    PathCombBucket *bucket;
    uint32_t i;

    if (comb->bucket_count) {
        PathCombBucket *tail = comb->buckets + (comb->bucket_count - 1);
        if (tail->suffix.len == suffix.len
                && (suffix.len == 0 || memcmp(tail->suffix.ptr, suffix.ptr, suffix.len) == 0)) {
            return tail;
        }
    }
    for (i = 0; i < comb->bucket_count; i++) {
        bucket = comb->buckets + i;
        if (bucket->suffix.len == suffix.len
                && (suffix.len == 0 || memcmp(bucket->suffix.ptr, suffix.ptr, suffix.len) == 0)) {
            return bucket;
        }
    }
    if (comb->bucket_count == comb->bucket_cap) {
        uint32_t cap = comb->bucket_cap ? comb->bucket_cap * 2 : 32;
        PathCombBucket *buckets = (PathCombBucket *)realloc(
                comb->buckets, (size_t)cap * sizeof(PathCombBucket));
        if (buckets == NULL) {
            return NULL;
        }
        comb->buckets = buckets;
        comb->bucket_cap = cap;
    }
    bucket = comb->buckets + comb->bucket_count;
    memset(bucket, 0, sizeof(*bucket));
    bucket->suffix = suffix;
    comb->bucket_count++;
    return bucket;
}

/*
 * The longest common suffix of the query suffix and one bucket suffix, the
 * length get_lcs_length() gives of the two: the level 3 of the search
 * scores the suffixes against each other, an empty query suffix scores the
 * whole path instead, see the reference.
 */
static uint32_t pathcomb_suffix_score(
        PathCombSlice query, const char *path, uint32_t path_len, PathCombSlice bucket)
{
    uint32_t bytes = 0;
    if (query.len) {
        return pathcomb_lcs(query.ptr, query.len, bucket.ptr, bucket.len, 0, 0, &bytes);
    }
    return pathcomb_lcs(path, path_len, bucket.ptr, bucket.len, 0, 0, &bytes);
}

/* ── the combined encoding, mirror of pathlen_coding.py ──────────────── */

/*
 * prefix reuse and remaining path length into one int, the three ranges
 * of _encode_prefix_comb_iter(): 5b+3b, biased 1B+1B, biased 2B+2B.
 */
static int pathcomb_prefix_combine(uint32_t zz, uint32_t path_len, uint32_t *out)
{
    uint64_t value;
    if (zz < 32 && path_len < 8) {
        *out = zz * 8 + path_len;
        return 0;
    }
    if (path_len < 256) {
        value = (uint64_t)zz * 256 + path_len + PATHCOMB_BIAS_1B1B;
        if (zz < 65535 && value <= 0xffffffffu) {
            *out = (uint32_t)value;
            return 0;
        }
    }
    value = (uint64_t)zz * 65536 + path_len + PATHCOMB_BIAS_2B2B;
    if (value > 0xffffffffu) {
        return PATHCOMB_ERR_RANGE;
    }
    *out = (uint32_t)value;
    return 0;
}

/*
 * suffix reuse and lookback into one int, the three ranges of
 * _encode_suffix_comb_iter(): 0, nibble, biased 1B+1B.
 */
static int pathcomb_suffix_combine(uint32_t reuse, uint32_t lookback, uint32_t *out)
{
    uint64_t value;
    if (reuse < 16 && lookback < 16) {
        *out = reuse * 16 + lookback;
        return 0;
    }
    value = (uint64_t)reuse * 256 + lookback + PATHCOMB_BIAS_1B1B;
    if (value > 0xffffffffu) {
        return PATHCOMB_ERR_RANGE;
    }
    *out = (uint32_t)value;
    return 0;
}

/*
 * The suffix reuse of a path and the lookback of the entry it reuses, the
 * reference get_lcs() of PathLookbackLCS:
 *
 *   1. the entries of the exact key, the members of the stop node of the
 *      path, from the newest to the oldest, the scan stops at the first
 *      entry out of reach of the lookback limit
 *   2. the classes of the same (suffix, last), the list of the node at
 *      that depth, the reuse is the suffix plus the last character
 *   3. the entries of the same suffix, the buckets of every suffix seen,
 *      the reuse is the common suffix of the query with the bucket suffix,
 *      the winner is the bucket that comes first among the longest ones
 *      that hold entries
 *
 * Each level picks the greatest index within reach: only the value of the
 * index is read again, so the order of the candidates of one level never
 * decides anything. out_bytes is the byte length of the reused characters.
 */
static void pathcomb_lookup(
        PathComb *comb, uint32_t index,
        const char *path, uint32_t path_len, uint32_t path_chars,
        PathCombSlice suffix, PathCombSlice last, PathCombSlice second,
        uint32_t key_bytes, uint32_t key_chars,
        uint32_t *out_lookback, uint32_t *out_length, uint32_t *out_bytes)
{
    PathCombNode *stop = comb->nodes + comb->stop_node[index];
    PathCombNode *klass;
    PathCombBucket *best_bucket = NULL;
    int64_t best_index = -1;
    uint32_t best_length = 0;
    uint32_t best_bytes = 0;
    uint32_t bytes = 0;
    uint32_t length;
    uint32_t k;

    /* level 1, the exact key, from the newest entry to the oldest */
    for (k = stop->filled; k > 0; k--) {
        PathCombMember *member = comb->members + stop->stop_first + (k - 1);
        const char *other = comb->paths + member->path_offset;
        if (index - member->index > PATHCOMB_MAX_SUFFIX_LOOKBACK) {
            break;
        }
        length = pathcomb_lcs(
                path, path_len, other, member->path_len, key_bytes, key_chars, &bytes);
        if (length < PATHCOMB_MIN_SUFFIX_REUSE) {
            continue;
        }
        if (length > PATHCOMB_MAX_SUFFIX_REUSE) {
            continue;
        }
        if (length == path_chars) {
            /* the whole path, the closest full match wins at once */
            *out_lookback = index - member->index;
            *out_length = length;
            *out_bytes = bytes;
            return;
        }
        if (length > best_length) {
            best_index = member->index;
            best_length = length;
            best_bytes = bytes;
        }
    }
    if (best_length) {
        *out_lookback = index - (uint32_t)best_index;
        *out_length = best_length;
        *out_bytes = best_bytes;
        return;
    }

    /* level 2, the same suffix and last character, any second character:
       the classes hang off the node of that (suffix, last) pair, the
       ancestor of the stop node at that depth, and the reuse is the suffix
       plus the last character */
    length = key_chars - (second.len ? 1u : 0u);
    klass = stop;
    while (klass->depth > suffix.len + last.len) {
        klass = comb->nodes + klass->parent;
    }
    if (length <= PATHCOMB_MAX_SUFFIX_REUSE) {
        for (k = klass->class_first; k; k = comb->nodes[k].l2_next) {
            PathCombNode *candidate = comb->nodes + k;
            uint32_t newest;
            if (!candidate->filled) {
                continue;
            }
            newest = comb->members[candidate->stop_first + candidate->filled - 1].index;
            if (index - newest > PATHCOMB_MAX_SUFFIX_LOOKBACK) {
                continue;
            }
            if ((int64_t)newest > best_index) {
                best_index = newest;
                best_length = length;
            }
        }
        if (best_index >= 0 && best_length >= PATHCOMB_MIN_SUFFIX_REUSE) {
            *out_lookback = index - (uint32_t)best_index;
            *out_length = best_length;
            *out_bytes = 0;
            return;
        }
    }

    /* level 3, the same suffix bucket, an empty bucket can not win */
    best_index = -1;
    best_length = 0;
    for (k = 0; k < comb->bucket_count; k++) {
        PathCombBucket *bucket = comb->buckets + k;
        length = pathcomb_suffix_score(suffix, path, path_len, bucket->suffix);
        if (length > PATHCOMB_MAX_SUFFIX_REUSE) {
            continue;
        }
        if (length > best_length) {
            if (!bucket->entries) {
                continue;
            }
            best_bucket = bucket;
            best_length = length;
        }
    }
    if (best_bucket) {
        for (k = best_bucket->first_group; k; k = comb->nodes[k].bucket_next) {
            PathCombNode *candidate = comb->nodes + k;
            uint32_t newest;
            if (!candidate->filled) {
                continue;
            }
            newest = comb->members[candidate->stop_first + candidate->filled - 1].index;
            if (index - newest > PATHCOMB_MAX_SUFFIX_LOOKBACK) {
                continue;
            }
            if ((int64_t)newest > best_index) {
                best_index = newest;
            }
        }
        if (best_index >= 0 && best_length >= PATHCOMB_MIN_SUFFIX_REUSE) {
            *out_lookback = index - (uint32_t)best_index;
            *out_length = best_length;
            *out_bytes = 0;
            return;
        }
    }

    *out_lookback = 0;
    *out_length = 0;
    *out_bytes = 0;
}

/* ── the entry points ────────────────────────────────────────────────── */

PATHCOMB_EXPORT int64_t abi_version(void);

PATHCOMB_EXPORT int64_t pathcomb_params(int64_t *out);

PATHCOMB_EXPORT int64_t pathcomb_encode_paths(
        const char *paths, int64_t n, uint32_t *prefix_comb, uint32_t *suffix_comb,
        uint8_t *remaining, int64_t capacity);

PATHCOMB_EXPORT int64_t pathcomb_encode_prefix_comb(
        const uint32_t *prefix_reuse, const uint32_t *path_length, int64_t n, uint32_t *out);

PATHCOMB_EXPORT int64_t pathcomb_encode_suffix_comb(
        const uint32_t *suffix_reuse, const uint32_t *suffix_lookback, int64_t n, uint32_t *out);

PATHCOMB_EXPORT int64_t abi_version(void)
{
    return PATHCOMB_ABI_VERSION;
}

/*
 * The frozen parameters of this library, for the caller to refuse a
 * library that speaks another pack format: the same signature with other
 * parameters would emit other bytes, silently.
 *
 * Args:
 *   out: 4 int64 values, MAX_PREFIX_REUSE, MIN_SUFFIX_REUSE,
 *        MAX_SUFFIX_REUSE, MAX_SUFFIX_LOOKBACK
 *
 * Returns:
 *   int64_t: the number of values written, 4
 */
PATHCOMB_EXPORT int64_t pathcomb_params(int64_t *out)
{
    if (out == NULL) {
        return PATHCOMB_ERR_ARGUMENT;
    }
    out[0] = PATHCOMB_MAX_PREFIX_REUSE;
    out[1] = PATHCOMB_MIN_SUFFIX_REUSE;
    out[2] = PATHCOMB_MAX_SUFFIX_REUSE;
    out[3] = PATHCOMB_MAX_SUFFIX_LOOKBACK;
    return 4;
}

/*
 * Encode the prefix reuse and the remaining path lengths into the combined
 * ints of encode_prefix_comb(): the differential + zigzag reuse combined
 * with the length, one int per entry.
 *
 * Args:
 *   prefix_reuse (const uint32_t *): Prefix reuse of every path
 *   path_length (const uint32_t *): Remaining path byte lengths
 *   n (int64_t): Number of entries
 *   out (uint32_t *): Out, n combined ints
 *
 * Returns:
 *   int64_t: 0, a negative error code otherwise, see PATHCOMB_ERR_*
 */
PATHCOMB_EXPORT int64_t pathcomb_encode_prefix_comb(
        const uint32_t *prefix_reuse, const uint32_t *path_length, int64_t n, uint32_t *out)
{
    int64_t i;
    uint32_t prev = 0;
    if (n < 0 || n > PATHCOMB_MAX_PATHS
            || (n > 0 && (prefix_reuse == NULL || path_length == NULL || out == NULL))) {
        return PATHCOMB_ERR_ARGUMENT;
    }
    for (i = 0; i < n; i++) {
        uint32_t reuse = prefix_reuse[i];
        uint32_t length = path_length[i];
        int32_t diff;
        uint32_t zz;
        if (reuse > PATHCOMB_MAX_PREFIX_REUSE || length > PATHCOMB_MAX_PATH_LEN) {
            return PATHCOMB_ERR_RANGE;
        }
        diff = (int32_t)reuse - (int32_t)prev;
        zz = diff >= 0 ? (uint32_t)diff * 2u : (uint32_t)(-diff) * 2u - 1u;
        if (pathcomb_prefix_combine(zz, length, out + i)) {
            return PATHCOMB_ERR_RANGE;
        }
        prev = reuse;
    }
    return 0;
}

/*
 * Encode the suffix reuse and the lookback distances into the combined ints
 * of encode_suffix_comb(), one int per entry.
 *
 * Args:
 *   suffix_reuse (const uint32_t *): Suffix reuse of every path
 *   suffix_lookback (const uint32_t *): Lookback distances
 *   n (int64_t): Number of entries
 *   out (uint32_t *): Out, n combined ints
 *
 * Returns:
 *   int64_t: 0, a negative error code otherwise, see PATHCOMB_ERR_*
 */
PATHCOMB_EXPORT int64_t pathcomb_encode_suffix_comb(
        const uint32_t *suffix_reuse, const uint32_t *suffix_lookback, int64_t n, uint32_t *out)
{
    int64_t i;
    if (n < 0 || n > PATHCOMB_MAX_PATHS
            || (n > 0 && (suffix_reuse == NULL || suffix_lookback == NULL || out == NULL))) {
        return PATHCOMB_ERR_ARGUMENT;
    }
    for (i = 0; i < n; i++) {
        uint32_t reuse = suffix_reuse[i];
        uint32_t lookback = suffix_lookback[i];
        if (reuse > PATHCOMB_MAX_SUFFIX_REUSE || lookback > PATHCOMB_MAX_SUFFIX_LOOKBACK) {
            return PATHCOMB_ERR_RANGE;
        }
        if (pathcomb_suffix_combine(reuse, lookback, out + i)) {
            return PATHCOMB_ERR_RANGE;
        }
    }
    return 0;
}

/* the arrays of one run, freed together, NULL or zero sized entries are off */
static void pathcomb_free(PathComb *comb)
{
    free(comb->nodes);
    free(comb->children);
    free(comb->members);
    free(comb->slot);
    free(comb->first_of);
    free(comb->path_offset);
    free(comb->path_len);
    free(comb->stop_node);
    free(comb->key_chars);
    free(comb->buckets);
}

/*
 * Encode the filepath section of a pack index.
 *
 * Args:
 *   paths (const char *): The paths, UTF-8, separated by NUL, in the
 *       encoded order. A path must not hold a NUL, which a validated pack
 *       path never does
 *   n (int64_t): Number of paths
 *   prefix_comb (uint32_t *): Out, n combined prefix values
 *   suffix_comb (uint32_t *): Out, n combined suffix values
 *   remaining (uint8_t *): Out, the remaining path bytes, concatenated
 *   capacity (int64_t): Capacity of the remaining buffer. The remaining
 *       paths are the paths with their reused prefix and suffix cut away,
 *       the total is never larger than the input
 *
 * Returns:
 *   int64_t: The bytes written to remaining, a negative error code
 *       otherwise, see PATHCOMB_ERR_*
 */
PATHCOMB_EXPORT int64_t pathcomb_encode_paths(
        const char *paths, int64_t n, uint32_t *prefix_comb, uint32_t *suffix_comb,
        uint8_t *remaining, int64_t capacity)
{
    PathComb comb;
    const char *path;
    const char *prev = "";
    uint32_t prev_len = 0;
    uint32_t prev_prefix = 0;
    uint32_t member_total = 0;
    int32_t i;
    int64_t written = 0;
    int error = 0;

    if (n < 0 || n > PATHCOMB_MAX_PATHS || capacity < 0) {
        return PATHCOMB_ERR_ARGUMENT;
    }
    if (n == 0) {
        return 0;
    }
    if (paths == NULL || prefix_comb == NULL || suffix_comb == NULL || remaining == NULL) {
        return PATHCOMB_ERR_ARGUMENT;
    }

    memset(&comb, 0, sizeof(comb));
    comb.paths = paths;
    comb.n = (int32_t)n;
    comb.slot = (uint32_t *)malloc((size_t)n * sizeof(uint32_t));
    comb.first_of = (uint32_t *)malloc((size_t)n * sizeof(uint32_t));
    comb.path_offset = (uint32_t *)malloc((size_t)n * sizeof(uint32_t));
    comb.path_len = (uint32_t *)malloc((size_t)n * sizeof(uint32_t));
    comb.stop_node = (uint32_t *)malloc((size_t)n * sizeof(uint32_t));
    comb.key_chars = (uint32_t *)malloc((size_t)n * sizeof(uint32_t));
    /* the root of the trie, the node index 0, the paths hang off it */
    comb.nodes = (PathCombNode *)calloc(1024, sizeof(PathCombNode));
    if (comb.nodes == NULL) {
        pathcomb_free(&comb);
        return PATHCOMB_ERR_ARGUMENT;
    }
    comb.node_cap = 1024;
    comb.node_count = 1;
    if (comb.slot == NULL || comb.first_of == NULL || comb.path_offset == NULL
            || comb.path_len == NULL || comb.stop_node == NULL || comb.key_chars == NULL) {
        pathcomb_free(&comb);
        return PATHCOMB_ERR_ARGUMENT;
    }

    /* the paths, and the first occurrence of every value: the reference keys
       its groups by the value of a path, so a path seen twice refreshes the
       index of the entry of its first occurrence instead of taking a second
       one, and the groups hold each value once */
    {
        const char *cursor = paths;
        for (i = 0; i < (int32_t)n; i++) {
            uint32_t len = (uint32_t)strlen(cursor);
            comb.path_offset[i] = (uint32_t)(cursor - paths);
            comb.path_len[i] = len;
            cursor += len + 1;
        }
    }
    if (pathcomb_mark_first(&comb)) {
        pathcomb_free(&comb);
        return PATHCOMB_ERR_ARGUMENT;
    }

    /* ── the layout pass: the trie, the groups, the member blocks ──────
       The keys and the group of a path depend on the paths alone, so the
       layout of every group is known before the search runs: the members
       of a group end up in one block, in insertion order, which is the
       order the reference keeps them in and the order the walk wants. */
    path = paths;
    for (i = 0; i < (int32_t)n; i++) {
        PathCombSlice suffix, last, second;
        PathCombNode *stop;
        PathCombBucket *bucket;
        uint32_t path_len = comb.path_len[i];
        uint32_t key_bytes;
        int32_t node;

        if (comb.first_of[i] != (uint32_t)i) {
            /* a path seen before, its node and its key are the ones of its
               first occurrence */
            comb.stop_node[i] = comb.stop_node[comb.first_of[i]];
            comb.key_chars[i] = comb.key_chars[comb.first_of[i]];
            continue;
        }
        path = comb.paths + comb.path_offset[i];
        pathcomb_key(path, path_len, &suffix, &last, &second);
        key_bytes = suffix.len + last.len + second.len;
        node = pathcomb_traverse(&comb, path, path_len, key_bytes);
        if (node < 0) {
            error = PATHCOMB_ERR_ARGUMENT;
            break;
        }
        stop = comb.nodes + (uint32_t)node;
        bucket = pathcomb_bucket(&comb, suffix);
        if (bucket == NULL) {
            error = PATHCOMB_ERR_ARGUMENT;
            break;
        }
        comb.stop_node[i] = (uint32_t)node;
        comb.key_chars[i] = pathcomb_strlen(suffix.ptr, suffix.len)
                + (last.len ? 1u : 0u) + (second.len ? 1u : 0u);
        if (!stop->linked) {
            /* the class list of the same (suffix, last) and the group list
               of the bucket hold every node once, in creation order */
            PathCombNode *klass = stop;
            while (klass->depth > key_bytes - second.len) {
                klass = comb.nodes + klass->parent;
            }
            stop->linked = 1;
            stop->l2_next = 0;
            if (klass->class_tail) {
                comb.nodes[klass->class_tail].l2_next = comb.stop_node[i];
            } else {
                klass->class_first = comb.stop_node[i];
            }
            klass->class_tail = comb.stop_node[i];
            {
                uint32_t node_index = comb.stop_node[i];
                stop->bucket = (uint32_t)(bucket - comb.buckets);
                stop->bucket_next = 0;
                if (bucket->tail_group) {
                    comb.nodes[bucket->tail_group].bucket_next = node_index;
                } else {
                    bucket->first_group = node_index;
                }
                bucket->tail_group = node_index;
                bucket->group_count++;
            }
        }
        stop->stop_count++;
        member_total++;
    }

    /* the member blocks, one per node, in group creation order */
    if (!error) {
        uint32_t offset = 0;
        uint32_t k;
        for (k = 0; k < comb.node_count; k++) {
            comb.nodes[k].stop_first = offset;
            offset += comb.nodes[k].stop_count;
        }
        if (member_total) {
            comb.members = (PathCombMember *)malloc((size_t)member_total * sizeof(PathCombMember));
            if (comb.members == NULL) {
                error = PATHCOMB_ERR_ARGUMENT;
            } else {
                comb.member_cap = member_total;
            }
        }
    }
    if (error) {
        pathcomb_free(&comb);
        return error;
    }

    /* ── the search pass ──────────────────────────────────────────────
       For every path: the search reads the entries of the paths before it,
       then the path is added, which refreshes the index of its entry when
       it is already there, exactly like the reference. */
    path = paths;
    for (i = 0; i < (int32_t)n; i++) {
        PathCombSlice suffix, last, second;
        PathCombNode *stop;
        uint32_t path_len;
        uint32_t path_chars;
        uint32_t key_bytes;
        uint32_t lookback = 0;
        uint32_t reuse = 0;
        uint32_t reuse_bytes = 0;
        uint32_t prefix_bytes = 0;
        uint32_t prefix_chars;
        uint32_t remaining_chars;
        uint32_t remaining_len;
        uint32_t slot;
        int32_t diff;
        uint32_t zz;

        path = comb.paths + comb.path_offset[i];
        path_len = comb.path_len[i];
        path_chars = pathcomb_strlen(path, path_len);

        prefix_chars = pathcomb_lcp(prev, prev_len, path, path_len, &prefix_bytes);
        if (prefix_chars > PATHCOMB_MAX_PREFIX_REUSE) {
            prefix_chars = PATHCOMB_MAX_PREFIX_REUSE;
            prefix_bytes = pathcomb_advance(path, path_len, prefix_chars);
        }
        remaining_chars = path_chars - prefix_chars;

        pathcomb_key(path, path_len, &suffix, &last, &second);
        key_bytes = suffix.len + last.len + second.len;
        stop = comb.nodes + comb.stop_node[i];
        pathcomb_lookup(
                &comb, (uint32_t)i, path, path_len, path_chars, suffix, last, second,
                key_bytes, comb.key_chars[i], &lookback, &reuse, &reuse_bytes);
        /* the suffix must fit the remaining path, a zero length reuse keeps
           no lookback: the decoder takes [-0:], the whole referenced path */
        if (reuse > remaining_chars) {
            reuse = remaining_chars;
            if (reuse == 0) {
                lookback = 0;
                reuse_bytes = 0;
            } else {
                reuse_bytes = path_len - pathcomb_advance(path, path_len, path_chars - reuse);
            }
        } else if (reuse && !reuse_bytes) {
            /* the levels that do not compare the whole path leave the
               measurement to the caller */
            reuse_bytes = path_len - pathcomb_advance(path, path_len, path_chars - reuse);
        }
        remaining_len = path_len - reuse_bytes - prefix_bytes;
        if (remaining_len > PATHCOMB_MAX_PATH_LEN) {
            error = PATHCOMB_ERR_RANGE;
            break;
        }
        if (written + remaining_len > capacity) {
            error = PATHCOMB_ERR_CAPACITY;
            break;
        }
        if (remaining_len) {
            memcpy(remaining + written, path + prefix_bytes, remaining_len);
        }
        written += remaining_len;

        diff = (int32_t)prefix_chars - (int32_t)prev_prefix;
        zz = diff >= 0 ? (uint32_t)diff * 2u : (uint32_t)(-diff) * 2u - 1u;
        if (pathcomb_prefix_combine(zz, remaining_len, prefix_comb + i)) {
            error = PATHCOMB_ERR_RANGE;
            break;
        }
        if (pathcomb_suffix_combine(reuse, lookback, suffix_comb + i)) {
            error = PATHCOMB_ERR_RANGE;
            break;
        }

        /* add: the entry of the path, refreshing the index of the entry of
           a path seen before instead of taking a second slot */
        if (comb.first_of[i] == (uint32_t)i) {
            slot = stop->stop_first + stop->filled;
            stop->filled++;
            comb.members[slot].path_offset = comb.path_offset[i];
            comb.members[slot].path_len = path_len;
            if (stop->filled == 1) {
                comb.buckets[stop->bucket].entries++;
            }
        } else {
            slot = comb.slot[comb.first_of[i]];
        }
        comb.members[slot].index = (uint32_t)i;
        comb.slot[i] = slot;

        prev = path;
        prev_len = path_len;
        prev_prefix = prefix_chars;
    }

    pathcomb_free(&comb);
    return error ? error : written;
}
