/*
 * bit2coding opcode encoder, DP over the exact byte cost of the format.
 *
 * The encoder turns a stream of small values (0~3, or 0~7 when ext8 is
 * enabled) into the opcode list described in
 * alasio/ext/algorithm/bit2coding/bit2coding_encode_python.py, and packs
 * that list into the bytes decode_bit2_stream_iter() reads back. Two entry
 * points are exported:
 *
 *   bit2_encode_opcodes  the opcode list alone, the interface of the
 *                        search itself, kept for tests and benchmarks
 *   bit2_encode_stream   the packed stream, the whole encoder, the opcode
 *                        list never crosses the language boundary
 *
 * Two things make this encoder different from the Python reference:
 *
 *   The cost model is exact. The Python reference prices a literal op as
 *   1 + ceil(k / 4) bytes and merges the literal ops of its traceback
 *   afterwards, so the bytes it predicts and the bytes it emits can differ
 *   by one per merged block. Here the literal op is not a transition of a
 *   fixed length, it is a state: the DP carries how many values the packed
 *   batch of the open literal op holds, and pays the staircase as the
 *   values are appended. What the DP minimises is therefore exactly what
 *   the emitter writes, and the emitted bytes of the C encoder are never
 *   more than the emitted bytes of the Python reference.
 *
 *   The prunings are free of any compression loss. They never change the
 *   encoded size of the unpruned search, which is the reference the tests
 *   compare against (lossless_prune = 0 runs the unpruned search):
 *
 *   1. Equal cost band pre-check. Every op type is a staircase of equal
 *      cost lengths (see below). dp never decreases, so when a band end
 *      is already strictly cheaper than the transition, no position
 *      inside the band can be improved and the whole band is skipped.
 *      Wide bands are relaxed from the first position they improve, found
 *      by binary search (dp never decreases), which keeps long runs at
 *      O(n log n) instead of O(n^2).
 *   2. Hash chain dominance. The chain visits match candidates from the
 *      closest offset to the farthest, so the copy cost never gets
 *      cheaper along the chain. A candidate whose match is not longer
 *      than the best match already seen cannot improve any dp position
 *      and is skipped, comparing at most best + 1 bytes to decide it.
 *      The chain walk also stops once the longest possible match of the
 *      position is reached, and it skips the rest of the run of the
 *      position in one step: every match inside a run ends where the run
 *      ends, and the run opcode covers those lengths for one byte.
 *
 * Tie updates (same cost, fewer literal values or ops) are only applied
 * in narrow bands. They pick between parses of the same size, so dropping
 * them never changes the encoded size, it only drops parse shapes that
 * would cost O(n) per band to keep.
 *
 * Byte cost model, matching encode_bit2_stream_iter():
 *   literal op: a packed batch of 1~2 values is a single byte, 3~34 values
 *               are 1 header byte plus ceil(k / 4) data bytes, so
 *               cost(k) = 1                        for k <= 2
 *               cost(k) = 1 + (k + 3) / 4          for 3 <= k <= 34
 *               Values above 3 (ext8) are stored as one single item byte
 *               each and they end the packed batch, see
 *               _encode_literal_iter().
 *   run op:     cost(l) = 1                        for 3 <= l < 35
 *               cost(l) = 2 + D(l - 35)            for l >= 35
 *   copy op:    cost(offset, l) = 2                for l <= 32, offset <= 256
 *               cost(offset, l) = 3 + D(l - 1) + D(offset - 1)   otherwise
 *   where D(v) is the extra byte count of a little endian length,
 *   D(v) = 0 for v <= 2^8-1, 1 for v <= 2^16-1, 2 for v <= 2^24-1, else 3.
 *
 * A run opcode carries its value in 2 bits, so a run is only available
 * for values 0~3, a copy opcode needs at least 3 values to be worth it.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define BIT2_EXPORT __declspec(dllexport)
#else
#define BIT2_EXPORT __attribute__((visibility("default")))
#endif

/*
 * Opcode layout, mirrors the tuples yielded by encode_bit2_opcode_iter():
 *   type 0 literal: a = offset of the values in the input, b = value count
 *   type 1 run:     a = run value, b = run length
 *   type 2 copy:    a = copy offset, b = copy length
 */
typedef struct {
    uint32_t type;
    uint32_t a;
    uint32_t b;
} Bit2Op;

#define BIT2_INF 0x3fffffff
/* 3 gram keys, 3 bits per value (ext8 allows 0~7) => 512 keys */
#define BIT2_CHAIN_SLOTS 1024
/* the smallest value count of a copy opcode */
#define BIT2_COPY_MIN 3
/* the largest packed batch of a literal op, 1 header byte + 9 data bytes */
#define BIT2_BATCH_MAX 34
/*
 * Literal states of the DP, the values the packed batch of the open literal
 * op holds:
 *   0            no packed batch pending, a literal op starts on the next value
 *   1 ~ 33       the open literal op holds that many values in its batch
 * A batch of 34 values costs the same as a batch of 33 plus a new one byte
 * op, so the DP wraps back to state 0 when the batch is filled.
 */
#define BIT2_LIT_STATES 34
/*
 * Bands wider than this are relaxed from the first position they improve
 * instead of scanning every length, see bit2_relax_band(). Ties do not
 * change the encoded size, so skipping their scan in wide bands is free.
 */
#define BIT2_TIE_SCAN_MAX 64

/*
 * Frozen configuration of the encoder. The search below stays
 * parameterised: the alternatives were measured while the encoder was
 * written (the plain DP, a limited hash chain, the literal tiebreak, see
 * doc/2026-09-27_bit2coding-c-encoder.md), the numbers say the free
 * choices all end at the same encoded size, and the frozen values are the
 * ones that make the encoder the reference every caller gets. They are not
 * an interface: changing one of them changes the bytes the encoder emits,
 * so it is a new encoder version, not a call parameter.
 *
 * BIT2_LOSSLESS_PRUNE is the exception, and the only switch the exports
 * take: the test suite turns it off to run the plain search and prove that
 * the prunings do not cost a single byte. Callers of production code use
 * the default, which is the value below.
 */
#define BIT2_MAX_CHAIN 0        /* 0 = no limit, search every candidate */
#define BIT2_LIT_TIEBREAK 0     /* off, the first best transition wins */
#define BIT2_LOSSLESS_PRUNE 1   /* on, the prunings never cost a byte */

/*
 * Version of the C interface, the exports, their arguments and their
 * meaning. Bump it whenever one of them changes: the wrapper refuses a
 * library of another version instead of calling it with the wrong
 * signature, and an old library does not export bit2_abi_version at all.
 */
#define BIT2_ABI_VERSION 2

/*
 * Extra bytes of a little endian length, the D of encode_length_int().
 */
static int32_t bit2_length_d(uint32_t value) {
    if (value <= 0xffu) {
        return 0;
    }
    if (value <= 0xffffu) {
        return 1;
    }
    if (value <= 0xffffffu) {
        return 2;
    }
    return 3;
}

/*
 * Largest value that still has the extra byte count d.
 */
static int32_t bit2_length_max(int32_t d) {
    if (d == 0) {
        return 0xff;
    }
    if (d == 1) {
        return 0xffff;
    }
    if (d == 2) {
        return 0xffffff;
    }
    return 0x7fffffff;
}

/*
 * Bytes of a packed literal batch holding s values, 1 <= s <= 34.
 */
static int32_t bit2_batch_cost(int32_t s) {
    if (s <= 2) {
        return 1;
    }
    return 1 + (s + 3) / 4;
}

/*
 * Append one value 0~3 to a packed batch holding s values.
 *
 * Args:
 *   s (int32_t): Values in the batch, 0 means the literal op starts here
 *   next (int32_t *): State after the append, 0 when the batch is full
 *   delta (int32_t *): Bytes the append adds
 */
static void bit2_batch_step(int32_t s, int32_t *next, int32_t *delta) {
    if (s >= BIT2_BATCH_MAX - 1) {
        /* the appended value fills the batch, which costs the same as
           ending the op here and starting a new one */
        *next = 0;
        *delta = bit2_batch_cost(BIT2_BATCH_MAX) - bit2_batch_cost(BIT2_BATCH_MAX - 1);
    } else {
        *next = s + 1;
        *delta = bit2_batch_cost(s + 1) - (s == 0 ? 0 : bit2_batch_cost(s));
    }
}

/*
 * Length of the common prefix of a and b, knowing that the first from
 * bytes are equal already. Comparison is done 8 bytes at a time, the
 * matches of a bit2 stream are long wherever a copy is worth it.
 *
 * Args:
 *   a, b (const uint8_t *): Buffers to compare
 *   from (int32_t): Known equal prefix, 0 to compare everything
 *   max_len (int32_t): Upper bound of the comparison
 *
 * Returns:
 *   int32_t: Common prefix length, at most max_len
 */
static int32_t bit2_match_length(const uint8_t *a, const uint8_t *b, int32_t from, int32_t max_len) {
    int32_t l = from;
    while (l + 8 <= max_len) {
        if (memcmp(a + l, b + l, 8) != 0) {
            break;
        }
        l += 8;
    }
    while (l < max_len && a[l] == b[l]) {
        l++;
    }
    return l;
}

/*
 * Relax a DP state.
 *
 * The reference keeps the first transition of the lowest cost. When the
 * tiebreak is enabled equal cost transitions are ordered by the number of
 * literal values and then by the number of literal ops, which keeps the
 * parse close to the Python reference: splitting a literal op only wins
 * when it saves bytes, never on a tie. Neither ordering changes the
 * encoded size, the cost is always the primary key.
 */
static void bit2_relax(
        int32_t *dp, int32_t *p_prev, int8_t *p_op, int32_t *p_arg,
        int32_t *p_lit, int32_t *p_ops, int32_t tiebreak,
        int32_t to, int32_t cost, int32_t prev, int32_t op, int32_t arg, int32_t lit, int32_t ops) {
    if (cost < dp[to]
            || (tiebreak && cost == dp[to]
                && (lit < p_lit[to] || (lit == p_lit[to] && ops < p_ops[to])))) {
        dp[to] = cost;
        p_prev[to] = prev;
        p_op[to] = (int8_t)op;
        p_arg[to] = arg;
        p_lit[to] = lit;
        p_ops[to] = ops;
    }
}

/*
 * Relax a band of lengths that all cost the same: they reach i + from to
 * i + to, the band end is the farthest position of the band.
 *
 * dp never decreases, so a transition strictly more expensive than the
 * band end is also strictly more expensive than every position inside the
 * band and the whole band can be skipped.
 *
 * Wide bands are relaxed from the first position they actually improve:
 * dp is non-decreasing, so the positions that are strictly improved form a
 * suffix of the band and binary search finds it in O(log n) instead of
 * scanning the whole band, which is what makes long runs cost O(n log n)
 * rather than O(n^2). Tie updates (same cost, fewer literal values or ops)
 * only change the parse, never the encoded size, and are only applied in
 * narrow bands, exactly like the band pre-checks of the Python reference
 * skip the ties of the bands they do not need.
 */
static void bit2_relax_band(
        int32_t *dp, int32_t *p_prev, int8_t *p_op, int32_t *p_arg,
        int32_t *p_lit, int32_t *p_ops, int32_t tiebreak, int32_t prune,
        int32_t i, int32_t from, int32_t to, int32_t cost, int32_t op, int32_t arg,
        int32_t lit, int32_t ops) {
    int32_t l;
    if (from > to) {
        return;
    }
    if (prune && cost > dp[i + to]) {
        return;
    }
    if (prune && to - from + 1 > BIT2_TIE_SCAN_MAX) {
        int32_t lo = from;
        int32_t hi = to;
        if (dp[i + to] <= cost) {
            /* even the farthest position of the band is not improved */
            return;
        }
        while (lo < hi) {
            int32_t mid = lo + (hi - lo) / 2;
            if (dp[i + mid] > cost) {
                hi = mid;
            } else {
                lo = mid + 1;
            }
        }
        for (l = lo; l <= to; l++) {
            bit2_relax(
                    dp, p_prev, p_op, p_arg, p_lit, p_ops, tiebreak, i + l, cost, i, op, arg, lit, ops);
        }
        return;
    }
    for (l = from; l <= to; l++) {
        bit2_relax(
                dp, p_prev, p_op, p_arg, p_lit, p_ops, tiebreak, i + l, cost, i, op, arg, lit, ops);
    }
}

/*
 * Relax the copy transitions of one chain entry, the lengths 3~lcp split
 * into the cost bands of encode_bit2_stream_iter(): the short format for
 * length <= 32 with offset <= 256, otherwise the long format
 * 3 + D(length - 1) + D(offset - 1), whose length part grows in steps of
 * 255, 65535 and 16777215 values.
 *
 * run_cover is the number of lengths the run transition of the position
 * already covers from the same start, 0 when there is no run opcode (the
 * value is above 3) or the run is shorter than 3. A run opcode costs 1
 * byte for the same lengths, a copy costs at least 2, so bands inside the
 * run are skipped.
 */
static void bit2_relax_copy(
        int32_t *dp, int32_t *p_prev, int8_t *p_op, int32_t *p_arg,
        int32_t *p_lit, int32_t *p_ops, int32_t tiebreak, int32_t prune,
        int32_t i, int32_t lcp, uint32_t offset, int32_t cur, int32_t cur_lit, int32_t cur_ops,
        int32_t run_cover) {
    /* long format base, the length part D(length - 1) is 0 up to 256 */
    int32_t base = cur + 3 + bit2_length_d(offset - 1u);
    /* the run opcode of this position covers the lengths up to run_cover for
       one byte, every copy length in there is at least one byte more */
    int32_t first = run_cover >= BIT2_COPY_MIN ? run_cover + 1 : BIT2_COPY_MIN;
    int32_t from = BIT2_COPY_MIN;

    if (lcp < from) {
        return;
    }
    if (offset <= 256u) {
        /* short format: 2 bytes for length 3~32 */
        int32_t to = lcp < 32 ? lcp : 32;
        int32_t lo = from > first ? from : first;
        if (lo <= to) {
            bit2_relax_band(
                    dp, p_prev, p_op, p_arg, p_lit, p_ops, tiebreak, prune, i,
                    lo, to, cur + 2, 2, (int32_t)offset, cur_lit, cur_ops);
        }
        from = 33;
        if (lcp < from) {
            return;
        }
    }
    /* long format, the length part D(length - 1) is 0 for length <= 256 */
    if (from <= 256) {
        int32_t to = lcp < 256 ? lcp : 256;
        int32_t lo = from > first ? from : first;
        if (lo <= to) {
            bit2_relax_band(
                    dp, p_prev, p_op, p_arg, p_lit, p_ops, tiebreak, prune, i,
                    lo, to, base, 2, (int32_t)offset, cur_lit, cur_ops);
        }
        from = 257;
    }
    /* long format with a growing length part */
    while (from <= lcp) {
        int32_t d = bit2_length_d((uint32_t)(from - 1));
        int64_t to = d >= 3 ? lcp : 1 + (int64_t)bit2_length_max(d);
        int32_t lo;
        if (to > lcp) {
            to = lcp;
        }
        lo = from > first ? from : first;
        if (lo <= (int32_t)to) {
            bit2_relax_band(
                    dp, p_prev, p_op, p_arg, p_lit, p_ops, tiebreak, prune, i,
                    lo, (int32_t)to, base + d, 2, (int32_t)offset, cur_lit, cur_ops);
        }
        from = (int32_t)to + 1;
    }
}

/*
 * Append one value to a literal op whose packed batch holds batch values.
 *
 * Args:
 *   cost (int32_t *): Bytes of the op so far, the append adds to it
 *   batch (int32_t *): Values in the packed batch, 0 when there is none
 *   value (uint8_t): Value to append
 */
static void bit2_literal_span_advance(int32_t *cost, int32_t *batch, uint8_t value) {
    if (value > 3) {
        /* a value above 3 is one item byte and it ends the packed batch */
        *cost += 1;
        *batch = 0;
    } else {
        int32_t next, delta;
        bit2_batch_step(*batch, &next, &delta);
        *cost += delta;
        *batch = next;
    }
}

/*
 * Merge adjacent literal ops when the merged op does not cost more bytes.
 *
 * The DP splits a literal run at packed batch boundaries because that is
 * what the staircase state gives it. Emitting two batches as one op or as
 * two ops can cost the same, and one op less is one tuple less for the
 * Python side, so the merge pass picks the cheaper of the two, which never
 * grows the encoded size.
 *
 * Args:
 *   data (const uint8_t *): Values of the input, to price the merged op
 *   out (Bit2Op *): Opcodes in encoding order, merged in place
 *   count (int64_t): Number of opcodes
 *
 * Returns:
 *   int64_t: Number of opcodes after the merge
 */
static int64_t bit2_merge_literals(const uint8_t *data, Bit2Op *out, int64_t count) {
    int64_t merged = 0;
    int64_t k;
    for (k = 0; k < count; k++) {
        uint32_t start, end, p;
        int32_t cost, batch;

        if (out[k].type != 0) {
            out[merged++] = out[k];
            continue;
        }
        start = out[k].a;
        end = start + out[k].b;
        cost = 0;
        batch = 0;
        for (p = start; p < end; p++) {
            bit2_literal_span_advance(&cost, &batch, data[p]);
        }

        while (k + 1 < count && out[k + 1].type == 0 && out[k + 1].a == end) {
            uint32_t next_end = out[k + 1].a + out[k + 1].b;
            /* the merged op continues the batch of this one, the separate
               option starts a new op, that difference is the whole point */
            int32_t grow = cost;
            int32_t grow_batch = batch;
            int32_t separate = 0;
            int32_t separate_batch = 0;
            uint32_t q;
            for (q = end; q < next_end; q++) {
                bit2_literal_span_advance(&grow, &grow_batch, data[q]);
                bit2_literal_span_advance(&separate, &separate_batch, data[q]);
            }
            if (grow > cost + separate) {
                break;
            }
            cost = grow;
            batch = grow_batch;
            end = next_end;
            k++;
        }

        out[merged].type = 0;
        out[merged].a = start;
        out[merged].b = end - start;
        merged++;
    }
    return merged;
}

/*
 * Encode values into opcodes with the DP over the exact byte cost.
 *
 * Args:
 *   data (const uint8_t *): values, one byte each, 0~3 (or 0~7 on ext8)
 *   n (int64_t): number of values
 *   out (Bit2Op *): output opcodes in encoding order, needs n + 1 slots
 *   capacity (int64_t): slots available in out
 *   max_chain (int64_t): hash chain steps to try per position, the DP
 *       needs no limit and 0 or a negative value means no limit
 *   lit_tiebreak (int64_t): non zero to break equal cost ties towards the
 *       path with fewer literal values
 *   lossless_prune (int64_t): non zero to enable the band pre-check and
 *       the chain dominance skip, none of them changes the encoded size
 *       of the unpruned search
 *
 * Returns:
 *   int64_t: number of opcodes written, -1 on invalid arguments or when
 *       the output does not fit in capacity
 */
static int64_t bit2_encode_ops(
        const uint8_t *data, int64_t n,
        Bit2Op *out, int64_t capacity, int64_t max_chain, int64_t lit_tiebreak,
        int64_t lossless_prune) {
    if (n < 0 || capacity < 0 || (n > 0 && (data == NULL || out == NULL))) {
        return -1;
    }
    if (n == 0) {
        return 0;
    }
    if (n > 0x7fffffff) {
        return -1;
    }

    int32_t count = (int32_t)n;
    int32_t cap = (int32_t)capacity;
    int tiebreak = lit_tiebreak != 0;
    int prune = lossless_prune != 0;

    /* one block for the tables: dp0, prev0, arg0, lit0, ops0, run_len, run_start,
       chain, chain_out, head */
    size_t words = (size_t)(5 * (count + 1) + 4 * count) + BIT2_CHAIN_SLOTS;
    int32_t *mem = (int32_t *)malloc(words * sizeof(int32_t));
    int8_t *op0 = (int8_t *)malloc((size_t)(count + 1) * sizeof(int8_t));
    if (mem == NULL || op0 == NULL) {
        free(mem);
        free(op0);
        return -1;
    }
    int32_t *dp0 = mem;
    int32_t *prev0 = dp0 + (count + 1);
    int32_t *arg0 = prev0 + (count + 1);
    int32_t *lit0 = arg0 + (count + 1);
    int32_t *ops0 = lit0 + (count + 1);
    int32_t *run_len = ops0 + (count + 1);
    int32_t *run_start = run_len + count;
    int32_t *chain = run_start + count;
    int32_t *chain_out = chain + count;
    int32_t *head = chain_out + count;

    /* the packed batch state of the open literal op, double buffered */
    int32_t state_cost[2][BIT2_LIT_STATES];
    int32_t state_lit[2][BIT2_LIT_STATES];
    int32_t state_ops[2][BIT2_LIT_STATES];

    int32_t i, j, s;

    /* run lengths of every position (backward scan) and the start of the
       maximal equal value run it belongs to (forward scan) */
    run_len[count - 1] = 1;
    for (i = count - 2; i >= 0; i--) {
        run_len[i] = data[i] == data[i + 1] ? run_len[i + 1] + 1 : 1;
    }
    run_start[0] = 0;
    for (i = 1; i < count; i++) {
        run_start[i] = data[i - 1] == data[i] ? run_start[i - 1] : i;
    }

    /* hash chains over 3 grams, forward scan, chain[i] is the previous
       position with the same 3 gram (or -1), unlimited match distance.
       chain_out[i] skips the rest of the run of i: the first chain entry
       outside of it, every entry in between sits in the same run and
       cannot match any longer than the run itself */
    for (i = 0; i < BIT2_CHAIN_SLOTS; i++) {
        head[i] = -1;
    }
    for (i = 0; i + 3 <= count; i++) {
        uint32_t key = ((uint32_t)data[i] << 6)
                | ((uint32_t)data[i + 1] << 3)
                | (uint32_t)data[i + 2];
        chain[i] = head[key];
        head[key] = i;
        chain_out[i] = chain[i] >= run_start[i] ? chain_out[chain[i]] : chain[i];
    }

    for (i = 0; i <= count; i++) {
        dp0[i] = BIT2_INF;
        prev0[i] = 0;
        arg0[i] = 0;
        lit0[i] = 0;
        ops0[i] = 0;
        op0[i] = 1;
    }
    dp0[0] = 0;
    for (s = 0; s < BIT2_LIT_STATES; s++) {
        state_cost[0][s] = BIT2_INF;
        state_lit[0][s] = 0;
        state_ops[0][s] = 0;
    }

    for (i = 0; i < count; i++) {
        int32_t *cur_state_cost = state_cost[i & 1];
        int32_t *cur_state_lit = state_lit[i & 1];
        int32_t *cur_state_ops = state_ops[i & 1];
        int32_t *nxt_state_cost = state_cost[(i + 1) & 1];
        int32_t *nxt_state_lit = state_lit[(i + 1) & 1];
        int32_t *nxt_state_ops = state_ops[(i + 1) & 1];

        for (s = 0; s < BIT2_LIT_STATES; s++) {
            nxt_state_cost[s] = BIT2_INF;
            nxt_state_lit[s] = 0;
            nxt_state_ops[s] = 0;
        }

        /* --- A. the closed state at i: the best way to have no pending
               batch, either a run/copy op (already stored) or closing the
               open literal op here (free, the staircase is paid) --- */
        int32_t cur = dp0[i];
        int32_t cur_lit = lit0[i];
        int32_t cur_ops = ops0[i];
        int32_t lit_len = -1;
        for (s = 1; s < BIT2_LIT_STATES; s++) {
            int32_t c = cur_state_cost[s];
            if (c >= BIT2_INF) {
                continue;
            }
            if (c < cur
                    || (tiebreak && c == cur
                        && (cur_state_lit[s] < cur_lit
                            || (cur_state_lit[s] == cur_lit && cur_state_ops[s] < cur_ops)))) {
                cur = c;
                cur_lit = cur_state_lit[s];
                cur_ops = cur_state_ops[s];
                lit_len = s;
            }
        }
        if (lit_len >= 0) {
            dp0[i] = cur;
            lit0[i] = cur_lit;
            ops0[i] = cur_ops;
            prev0[i] = i;
            arg0[i] = lit_len;
            op0[i] = 0;
        }

        /* --- B. run transitions, a run opcode carries 2 bits of value --- */
        if (cur < BIT2_INF) {
            uint32_t value = data[i];
            if (value <= 3u && run_len[i] >= 3) {
                int32_t run_max = run_len[i];
                int32_t from = 3;
                while (from <= run_max) {
                    int32_t to, cost;
                    if (from < 35) {
                        /* 1XXNNNNN: 1 byte for 3~34 values */
                        to = 34;
                        cost = cur + 1;
                    } else {
                        /* 0110XXDD: 2 + D(run - 35) bytes */
                        int32_t d = bit2_length_d((uint32_t)(from - 35));
                        int64_t end = 35 + (int64_t)bit2_length_max(d);
                        to = end > run_max ? run_max : (int32_t)end;
                        cost = cur + 2 + d;
                    }
                    if (to > run_max) {
                        to = run_max;
                    }
                    bit2_relax_band(
                            dp0, prev0, op0, arg0, lit0, ops0, tiebreak, prune, i,
                            from, to, cost, 1, (int32_t)value, cur_lit, cur_ops);
                    from = to + 1;
                }
            }

            /* --- C. copy transitions, every earlier 3 gram match --- */
            if (i + 3 <= count) {
                int64_t steps = 0;
                /* lengths the run opcode of this position covers for 1 byte */
                int32_t run_cover = (value <= 3u && run_len[i] >= 3) ? run_len[i] : 0;
                /* best match found outside of the run of i so far */
                int32_t best_out = 0;
                j = chain[i];
                while (j >= 0 && (max_chain <= 0 || steps < max_chain)) {
                    uint32_t offset = (uint32_t)(i - j);
                    int32_t match_max = count - i;
                    int32_t lcp;

                    if (prune && run_cover > 0) {
                        /* Every candidate that sits in an equal value run has a
                           match of exactly min(remain_i, remain_j) bytes (both
                           runs end where their value changes), and that never
                           passes remain_i = run_cover, which the run opcode
                           already covers for one byte. So of every equal value
                           run only the candidate whose remaining run length is
                           exactly run_cover can matter; the rest is skipped
                           without comparing a single byte. */
                        int32_t rj = run_len[j];
                        if (rj != run_cover) {
                            if (rj < run_cover) {
                                int32_t skip = run_cover - rj;
                                if (j - skip >= run_start[j]) {
                                    /* the one candidate of this run that can escape */
                                    j -= skip;
                                    steps++;
                                    continue;
                                }
                            }
                            /* the rest of this run is dominated */
                            j = chain_out[j];
                            steps++;
                            continue;
                        }
                    }
                    if (prune) {
                        /* a candidate in a run of the same value shares the
                           whole shorter run with i, those bytes need no
                           comparison; and a match no longer than the best
                           one seen cannot improve any position */
                        int32_t known = run_len[j] < run_len[i] ? run_len[j] : run_len[i];
                        int32_t bound = best_out + 1;
                        if (known < 3) {
                            known = 3;
                        }
                        if (bound < known) {
                            bound = known;
                        }
                        if (bound > match_max) {
                            bound = match_max;
                        }
                        lcp = bit2_match_length(data + i, data + j, known, bound);
                        if (lcp >= bound && bound < match_max) {
                            /* longer than the best one, get the full length */
                            lcp = bit2_match_length(data + i, data + j, lcp, match_max);
                        }
                        if (lcp <= best_out) {
                            j = chain[j];
                            steps++;
                            continue;
                        }
                        best_out = lcp;
                    } else {
                        lcp = bit2_match_length(data + i, data + j, 3, match_max);
                    }

                    bit2_relax_copy(
                            dp0, prev0, op0, arg0, lit0, ops0, tiebreak, prune,
                            i, lcp, offset, cur, cur_lit, cur_ops, run_cover);

                    if (prune && best_out >= match_max) {
                        /* the longest match this position can have is reached,
                           the remaining entries are not longer and not cheaper */
                        break;
                    }
                    j = chain[j];
                    steps++;
                }
            }
        }

        /* --- D. literal transitions, one value at a time, priced by the
               exact staircase of the packed batch --- */
        if (i + 1 <= count) {
            for (s = 0; s < BIT2_LIT_STATES; s++) {
                int32_t c, l, o;
                if (s == 0) {
                    c = cur;
                    l = cur_lit;
                    o = cur_ops;
                } else {
                    c = cur_state_cost[s];
                    l = cur_state_lit[s];
                    o = cur_state_ops[s];
                }
                if (c >= BIT2_INF) {
                    continue;
                }
                /* opening a literal op adds one value and one op, the values
                   appended to an open op add a value only */
                {
                    int32_t lit = l + 1;
                    int32_t ops = o + (s == 0 ? 1 : 0);
                    if (data[i] <= 3) {
                        int32_t next, delta;
                        bit2_batch_step(s, &next, &delta);
                        if (next == 0) {
                            /* the appended value fills the batch, the op ends */
                            bit2_relax(
                                    dp0, prev0, op0, arg0, lit0, ops0, tiebreak, i + 1,
                                    c + delta, i, 0, s + 1, lit, ops);
                        } else {
                            int32_t cost = c + delta;
                            if (cost < nxt_state_cost[next]
                                    || (tiebreak && cost == nxt_state_cost[next]
                                        && (lit < nxt_state_lit[next]
                                            || (lit == nxt_state_lit[next] && ops < nxt_state_ops[next])))) {
                                nxt_state_cost[next] = cost;
                                nxt_state_lit[next] = lit;
                                nxt_state_ops[next] = ops;
                            }
                        }
                    } else {
                        /* a value above 3 is a single item byte and it ends the
                           packed batch, the literal op ends here too */
                        bit2_relax(
                                dp0, prev0, op0, arg0, lit0, ops0, tiebreak, i + 1,
                                c + 1, i, 0, s + 1, lit, ops);
                    }
                }
            }
        }
    }

    /* the parse ends at count, the open literal op has to be closed there       as well, the loop above only closes at the positions an op can start */
    {
        int32_t *cur_state_cost = state_cost[count & 1];
        int32_t *cur_state_lit = state_lit[count & 1];
        int32_t *cur_state_ops = state_ops[count & 1];
        int32_t cur = dp0[count];
        int32_t cur_lit = lit0[count];
        int32_t cur_ops = ops0[count];
        for (s = 1; s < BIT2_LIT_STATES; s++) {
            int32_t c = cur_state_cost[s];
            if (c >= BIT2_INF) {
                continue;
            }
            if (c < cur
                    || (tiebreak && c == cur
                        && (cur_state_lit[s] < cur_lit
                            || (cur_state_lit[s] == cur_lit && cur_state_ops[s] < cur_ops)))) {
                cur = c;
                cur_lit = cur_state_lit[s];
                cur_ops = cur_state_ops[s];
                dp0[count] = cur;
                lit0[count] = cur_lit;
                ops0[count] = cur_ops;
                prev0[count] = count;
                arg0[count] = s;
                op0[count] = 0;
            }
        }
        if (dp0[count] >= BIT2_INF) {
            /* this shouldn't happen, literals cover every input */
            free(mem);
            free(op0);
            return -1;
        }
    }

    /* --- traceback ---
       op0 0: a literal op of arg0 values ending at this position
       op0 1: a run op of arg0 values (the run value) starting at prev0
       op0 2: a copy op of offset arg0 starting at prev0 */
    int64_t out_count = 0;
    int32_t pos = count;
    while (pos > 0) {
        int32_t prev = prev0[pos];
        int32_t op = op0[pos];
        if (out_count >= cap) {
            free(mem);
            free(op0);
            return -1;
        }
        out[out_count].type = (uint32_t)op;
        if (op == 0) {
            out[out_count].a = (uint32_t)(pos - arg0[pos]);
            out[out_count].b = (uint32_t)arg0[pos];
        } else {
            out[out_count].a = (uint32_t)arg0[pos];
            out[out_count].b = (uint32_t)(pos - prev);
            if (prev >= pos) {
                /* this shouldn't happen, every op covers at least one value */
                free(mem);
                free(op0);
                return -1;
            }
        }
        out_count++;
        pos = op == 0 ? pos - arg0[pos] : prev;
    }

    free(mem);
    free(op0);

    /* the traceback walks the parse backward, restore the encoding order */
    for (int64_t k = 0; k < out_count / 2; k++) {
        Bit2Op tmp = out[k];
        out[k] = out[out_count - 1 - k];
        out[out_count - 1 - k] = tmp;
    }

    out_count = bit2_merge_literals(data, out, out_count);

    return out_count;
}

/*
 * Version of the C interface of the library, see BIT2_ABI_VERSION. Every
 * library of the package exports this name, the loader of the package
 * (alasio_speedup/_library.py) refuses a library that does not declare it.
 *
 * Returns:
 *   int64_t: ABI version of the library
 */
BIT2_EXPORT int64_t abi_version(void) {
    return BIT2_ABI_VERSION;
}

/*
 * Encode values into opcodes with the DP over the exact byte cost, the
 * search without the emitter. Packing the opcodes into the stream is
 * bit2_emit_stream(), see the module comment.
 *
 * Args:
 *   data (const uint8_t *): values, one byte each, 0~3 (or 0~7 on ext8)
 *   n (int64_t): number of values
 *   out (Bit2Op *): output opcodes in encoding order, needs n + 1 slots
 *   capacity (int64_t): slots available in out
 *   lossless_prune (int64_t): non zero to enable the lossless prunings,
 *       the frozen configuration, the tests turn them off to compare
 *
 * Returns:
 *   int64_t: number of opcodes written, -1 on invalid arguments or when
 *       the output does not fit in capacity
 */
BIT2_EXPORT int64_t bit2_encode_opcodes(
        const uint8_t *data, int64_t n, Bit2Op *out, int64_t capacity, int64_t lossless_prune) {
    return bit2_encode_ops(
            data, n, out, capacity, BIT2_MAX_CHAIN, BIT2_LIT_TIEBREAK, lossless_prune != 0);
}

/*
 * Output buffer of the stream emitter, bounds checked: an overflowing write
 * marks the writer and drops the byte, bit2_encode_stream() reports it as a
 * failure instead of writing out of the buffer.
 */
typedef struct {
    uint8_t *out;
    int64_t capacity;
    int64_t count;
    int32_t overflow;
} Bit2Writer;

/*
 * Append one byte to the stream.
 *
 * Args:
 *   w (Bit2Writer *): Writer of the output stream
 *   value (uint8_t): Byte to append
 */
static void bit2_put(Bit2Writer *w, uint8_t value) {
    if (w->count >= w->capacity) {
        w->overflow = 1;
        return;
    }
    w->out[w->count++] = value;
}

/*
 * Append a length as d + 1 little endian bytes, the format of
 * encode_length_int() of the Python reference.
 *
 * Args:
 *   w (Bit2Writer *): Writer of the output stream
 *   value (uint32_t): Length to append
 *   d (int32_t): Extra bytes of the length, 0~3
 */
static void bit2_put_length(Bit2Writer *w, uint32_t value, int32_t d) {
    int32_t k;
    for (k = 0; k <= d; k++) {
        bit2_put(w, (uint8_t)(value & 0xffu));
        value >>= 8;
    }
}

/*
 * Append a packed batch of 1 ~ 34 values 0~3, the literal formats of
 * _encode_literal_iter():
 *   000000XX: 1 item
 *   0001XXYY: 2 item
 *   001NNNNN: N + 3 items, N (0~31), one header byte plus ceil(N/4) data
 *             bytes, the last one padded with trailing 00
 *
 * Args:
 *   w (Bit2Writer *): Writer of the output stream
 *   values (const uint8_t *): Values of the batch, 1 ~ 34 of them
 *   count (uint32_t): Number of values in the batch
 */
static void bit2_put_batch(Bit2Writer *w, const uint8_t *values, uint32_t count) {
    uint32_t k;
    if (count == 1) {
        bit2_put(w, values[0]);
        return;
    }
    if (count == 2) {
        bit2_put(w, (uint8_t)(16 + values[0] * 4 + values[1]));
        return;
    }
    bit2_put(w, (uint8_t)(29 + count));
    for (k = 0; k < count; k += 4) {
        /* 4 values per byte, most significant pair first, AABBCCDD */
        uint32_t packed = (uint32_t)values[k] << 6;
        if (k + 1 < count) {
            packed |= (uint32_t)values[k + 1] << 4;
        }
        if (k + 2 < count) {
            packed |= (uint32_t)values[k + 2] << 2;
        }
        if (k + 3 < count) {
            packed |= (uint32_t)values[k + 3];
        }
        bit2_put(w, (uint8_t)packed);
    }
}

/*
 * Append a run of values 0~3 as packed batches of at most BIT2_BATCH_MAX
 * values each, the batch size of _encode_literal_iter().
 *
 * Args:
 *   w (Bit2Writer *): Writer of the output stream
 *   values (const uint8_t *): Values to append
 *   count (uint32_t): Number of values
 */
static void bit2_put_batches(Bit2Writer *w, const uint8_t *values, uint32_t count) {
    uint32_t done = 0;
    while (done < count) {
        uint32_t left = count - done;
        uint32_t take = left < BIT2_BATCH_MAX ? left : BIT2_BATCH_MAX;
        bit2_put_batch(w, values + done, take);
        done += take;
    }
}

/*
 * Append a literal opcode: the values are packed into batches of at most
 * BIT2_BATCH_MAX values, and with ext8 on, a value 4~7 is a single item
 * byte (000001XX) that ends the packed batch, exactly like
 * encode_bit2_stream_iter() splits them.
 *
 * Args:
 *   w (Bit2Writer *): Writer of the output stream
 *   values (const uint8_t *): Values of the literal op
 *   count (uint32_t): Number of values
 *   ext8 (int32_t): Non zero when the values may be 4~7
 */
static void bit2_put_literal(Bit2Writer *w, const uint8_t *values, uint32_t count, int32_t ext8) {
    uint32_t start = 0;
    uint32_t k;
    if (!ext8) {
        bit2_put_batches(w, values, count);
        return;
    }
    for (k = 0; k < count; k++) {
        if (values[k] <= 3) {
            continue;
        }
        bit2_put_batches(w, values + start, k - start);
        bit2_put(w, values[k]);
        start = k + 1;
    }
    bit2_put_batches(w, values + start, count - start);
}

/*
 * Pack an opcode list into the bit2 stream, the format of
 * encode_bit2_stream_iter() of the Python reference.
 *
 * Args:
 *   data (const uint8_t *): Values of the input, the literal opcodes
 *       index it
 *   ops (const Bit2Op *): Opcodes in encoding order
 *   count (int64_t): Number of opcodes
 *   out (uint8_t *): Output buffer
 *   capacity (int64_t): Bytes available in out
 *   ext8 (int32_t): Non zero when the input may hold values 4~7
 *
 * Returns:
 *   int64_t: Number of bytes written, -1 when the output does not fit in
 *       capacity or an opcode is invalid
 */
static int64_t bit2_emit_stream(
        const uint8_t *data, const Bit2Op *ops, int64_t count,
        uint8_t *out, int64_t capacity, int32_t ext8) {
    Bit2Writer w;
    int64_t k;

    w.out = out;
    w.capacity = capacity;
    w.count = 0;
    w.overflow = 0;

    for (k = 0; k < count; k++) {
        uint32_t type = ops[k].type;
        uint32_t a = ops[k].a;
        uint32_t b = ops[k].b;

        if (type == 0) {
            /* literal, a = offset of the values, b = value count */
            bit2_put_literal(&w, data + a, b, ext8);
        } else if (type == 1) {
            /* run, a = run value 0~3, b = run length, at least 3 */
            if (b < 35) {
                /* 1XXNNNNN: run XX for N+3 times, N (0~31) */
                bit2_put(&w, (uint8_t)(128 + a * 32 + (b - 3)));
            } else {
                /* 0110XXDD: run XX for N+35 times, N (0~2^32) */
                int32_t d = bit2_length_d(b - 35);
                bit2_put(&w, (uint8_t)(96 + a * 4 + d));
                bit2_put_length(&w, b - 35, d);
            }
        } else if (type == 2) {
            /* copy, a = copy offset, b = copy length, at least 3 */
            if (b <= 32 && a <= 256) {
                /* 010LLLLL: copy from offset F+1 length L+1, F in one byte */
                bit2_put(&w, (uint8_t)(63 + b));
                bit2_put(&w, (uint8_t)(a - 1));
            } else {
                /* 0111LLFF: copy, length and offset in variable length ints */
                int32_t l_d = bit2_length_d(b - 1);
                int32_t f_d = bit2_length_d(a - 1);
                bit2_put(&w, (uint8_t)(112 + l_d * 4 + f_d));
                bit2_put_length(&w, b - 1, l_d);
                bit2_put_length(&w, a - 1, f_d);
            }
        } else {
            /* this shouldn't happen */
            return -1;
        }

        if (w.overflow) {
            return -1;
        }
    }

    return w.count;
}

/*
 * Encode values into the bit2 stream, the whole encoder in one call: the
 * opcode DP and the packing of its opcodes into bytes.
 *
 * Args:
 *   data (const uint8_t *): Values, one byte each, 0~3 (or 0~7 on ext8)
 *   n (int64_t): Number of values
 *   out (uint8_t *): Output stream, the vint count prefix of the Python
 *       encode_bit2() is not included
 *   capacity (int64_t): Bytes available in out. The stream never needs
 *       more than n + 1 bytes: the search always has the all literal
 *       parse, which packs n values into at most n + 1 bytes
 *   ext8 (int64_t): Non zero when the input may hold values 4~7, the
 *       format of the data, not a tuning knob
 *   lossless_prune (int64_t): Non zero to enable the lossless prunings,
 *       the frozen configuration, the tests turn them off to compare
 *
 * Returns:
 *   int64_t: Number of bytes written, -1 on invalid arguments or when the
 *       output does not fit in capacity
 */
BIT2_EXPORT int64_t bit2_encode_stream(
        const uint8_t *data, int64_t n, uint8_t *out, int64_t capacity, int64_t ext8,
        int64_t lossless_prune) {
    Bit2Op *ops;
    int64_t count;
    int64_t written;

    if (n < 0 || capacity < 0 || (n > 0 && (data == NULL || out == NULL))) {
        return -1;
    }
    if (n == 0) {
        return 0;
    }

    /* one opcode consumes at least one value, n + 1 slots always fit */
    ops = (Bit2Op *)malloc((size_t)(n + 1) * sizeof(Bit2Op));
    if (ops == NULL) {
        return -1;
    }
    count = bit2_encode_ops(
            data, n, ops, n + 1, BIT2_MAX_CHAIN, BIT2_LIT_TIEBREAK, lossless_prune != 0);
    if (count < 0) {
        free(ops);
        return -1;
    }

    written = bit2_emit_stream(data, ops, count, out, capacity, ext8 != 0);
    free(ops);
    return written;
}
