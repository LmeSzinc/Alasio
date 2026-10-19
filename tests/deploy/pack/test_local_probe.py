"""
Tests for the local presence probe: what a local tree supplies for an
index pack, the accounting per stored block, the path decision, and the
early stop of the largest-first scan.

The packs are built from MockGitRepo like the job tests (a pack built by
PackFull carries the generated .pack/history.pack record and the deleted
markers of folders without __init__.py, they take part in the
accounting like any other record). Every test runs in the in-memory fake
filesystem, no real files are written.
"""
import os
import stat

import pytest

from alasio.deploy.pack import local_probe
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.local_probe import PRESENT_RATIO, REQUEST_BUDGET, LocalProbe, PresenceResult, stored_blocks
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import random_bytes

# the tree the probe tests write into, inside the fake filesystem
ROOT = '/repo'


def make_pack(files, commit='c1'):
    """
    Build a full pack of a version.

    Args:
        files (dict[str, bytes]): {path: content}
        commit (str): Version of the pack. Defaults to 'c1'.

    Returns:
        bytes: Full pack data
    """
    repo = MockGitRepo()
    for path, content in files.items():
        repo.register_file(commit, path, content)
    repo.register_commit(commit, author_name='Author', message='')
    return b''.join(PackFull(repo, commit=commit).iter_pack_data())


def spec_blocks(decoder):
    """
    The distinct stored blocks of a decoder, the accounting rule stated
    independently of the probe: records grouped by their stored range,
    deleted markers and records without data left out.

    Args:
        decoder (PackDecodeBase): Decoder of the pack

    Returns:
        dict[tuple[int, int], list[str]]: {(data_start, data_size): [path]}
    """
    blocks: "dict[tuple[int, int], list[str]]" = {}
    for path, info in decoder.fileinfo.items():
        if info.edit == 2 or not info.data_size:
            continue
        blocks.setdefault((info.data_start, info.data_size), []).append(path)
    return blocks


def spec_presence(decoder, root):
    """
    The complete accounting of a tree, stated independently of the probe:
    every block of the spec is checked, no early stop. The tests compare
    the verdict of a partial scan with the verdict of this accounting.

    Args:
        decoder (PackDecodeBase): Decoder of the pack
        root (str): Root folder of the tree

    Returns:
        PresenceResult: The complete accounting
    """
    spec = spec_blocks(decoder)
    total_bytes = sum(size for _, size in spec)
    present_bytes = 0
    missing_blocks = 0
    for (_, data_size), paths in spec.items():
        if all(_spec_present(f'{root}/{path}') for path in paths):
            present_bytes += data_size
        else:
            missing_blocks += 1
    return PresenceResult(
        present_bytes=present_bytes, total_bytes=total_bytes, missing_blocks=missing_blocks)


def _spec_present(path):
    """
    Args:
        path (str): Path to check

    Returns:
        bool: True when a regular file exists at the path
    """
    try:
        st = os.stat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def write_tree(decoder, fs, exclude=()):
    """
    Write every record of a decoder to the fake filesystem.

    The content of every record is written to its path (a C record
    decodes to the content of its source), deleted markers are left out:
    the tree is exactly the unpacked version minus exclude.

    Args:
        decoder (PackDecodeBase): Decoder of the pack
        fs (FakeFilesystem): The active fake filesystem
        exclude (tuple[str]): Paths to leave out
    """
    for path, info in decoder.fileinfo.items():
        if info.edit == 2 or path in exclude:
            continue
        fs.create_file(f'{ROOT}/{path}', contents=bytes(decoder.catfile(info)))


# a pack whose records cover the block shapes of the accounting: a C
# record sharing the stored block of its source (b.txt copies a.txt), a
# unique content (c.txt), an empty file (a record without data) and a
# .py file whose folder has no __init__.py (an auto deleted marker)
PROBE_FILES = {
    'a.txt': b'alpha\n' * 100,
    'b.txt': b'alpha\n' * 100,
    'c.txt': b'beta\n' * 50,
    'empty.txt': b'',
    'tools/helper.py': b'def helper():\n    return 42\n',
}
PROBE_PACK = make_pack(PROBE_FILES)
PROBE_DECODER = PackDecodeBase(PROBE_PACK)
# the index pack of the version, the input of the rebuild flow: it
# describes the same records and blocks as the full pack
PROBE_INDEX_DECODER = PackDecodeBase(bytes(PROBE_DECODER.extract_index_pack()))
PROBE_SPEC = spec_blocks(PROBE_DECODER)
PROBE_TOTAL = sum(size for _, size in PROBE_SPEC)
# the block shared by a.txt and its C record
SHARED_SIZE = PROBE_DECODER.fileinfo['a.txt'].data_size

# the budget guard pack: two large blocks and three small ones, for the
# trees that supply most of the stored bytes but miss blocks: the byte
# ratio stays above its bound while the missing block count decides
GUARD_FILES = {
    'big1.bin': random_bytes(20000, 1),
    'big2.bin': random_bytes(20000, 2),
    'small1.bin': random_bytes(200, 11),
    'small2.bin': random_bytes(200, 12),
    'small3.bin': random_bytes(200, 13),
}
GUARD_DECODER = PackDecodeBase(make_pack(GUARD_FILES))
GUARD_MISSING = ('small1.bin', 'small2.bin', 'small3.bin')

# the early stop pack: eight large blocks carrying 99% of the stored
# bytes and a hundred small records behind them, the shape of a real
# pack (its largest 10% of records carry ~80% of the bytes)
SKEW_BIGS = [f'big{i}.bin' for i in range(8)]
SKEW_FILES = {path: random_bytes(30000, 100 + i) for i, path in enumerate(SKEW_BIGS)}
SKEW_FILES.update({f'small{i}.txt': f'small content {i}\n'.encode() for i in range(100)})
SKEW_DECODER = PackDecodeBase(make_pack(SKEW_FILES))

# the last-block pack: ten equal stored blocks plus the small history
# record. Two of the ten are present, so the ratio bound is crossed on
# the very last block of the scan and no block is left unchecked
LAST_FILES = {f'f{i}.bin': random_bytes(1000, 700 + i) for i in range(10)}
LAST_DECODER = PackDecodeBase(make_pack(LAST_FILES))


class TestStoredBlocks:
    """stored_blocks(): the records of one stored block are grouped into
    one entry."""

    def test_shared_block_single_entry(self):
        """A C record and its source are one block carrying both paths."""
        blocks = {tuple(block.paths): block for block in stored_blocks(PROBE_DECODER)}
        block = blocks[('a.txt', 'b.txt')]
        assert block.data_start == PROBE_DECODER.fileinfo['a.txt'].data_start
        assert block.data_size == PROBE_DECODER.fileinfo['a.txt'].data_size

    def test_data_less_records_have_no_block(self):
        """Empty files and the deleted markers of the pack take part in
        no block: nothing is fetched for them either way."""
        paths = {path for block in stored_blocks(PROBE_DECODER) for path in block.paths}
        assert 'empty.txt' not in paths
        assert PROBE_DECODER.fileinfo['empty.txt'].data_size == 0
        assert 'tools/__init__.py' not in paths
        assert PROBE_DECODER.fileinfo['tools/__init__.py'].edit == 2

    def test_blocks_match_the_accounting_rule(self):
        """The blocks are the distinct stored ranges, in data offset order."""
        blocks = stored_blocks(PROBE_DECODER)
        assert [(block.data_start, block.data_size) for block in blocks] == sorted(PROBE_SPEC)
        assert {tuple(block.paths) for block in blocks} == {
            tuple(paths) for paths in PROBE_SPEC.values()}
        assert sum(block.data_size for block in blocks) == PROBE_TOTAL

    def test_index_pack_same_blocks_as_full_pack(self):
        """The index pack of a version describes the same blocks as its
        full pack: the probe of the rebuild flow works on the index pack."""
        assert [
            (block.data_start, block.data_size, block.paths)
            for block in stored_blocks(PROBE_INDEX_DECODER)
        ] == [
            (block.data_start, block.data_size, block.paths)
            for block in stored_blocks(PROBE_DECODER)
        ]


class TestPresence:
    """LocalProbe.presence(): every record path is checked, the accounting
    is per block."""

    def test_full_tree(self, fs):
        """A tree holding every record supplies every stored byte: the
        scan reaches the end."""
        write_tree(PROBE_DECODER, fs)
        result = LocalProbe(PROBE_DECODER).presence(ROOT)
        assert result == PresenceResult(
            present_bytes=PROBE_TOTAL, total_bytes=PROBE_TOTAL, missing_blocks=0)

    def test_empty_root(self, fs):
        """An empty root supplies nothing: the one wave of the small pack is
        checked and the full pack is decided."""
        fs.create_dir(ROOT)
        result = LocalProbe(PROBE_DECODER).presence(ROOT)
        assert result == PresenceResult(
            present_bytes=0, total_bytes=PROBE_TOTAL, missing_blocks=4, complete=True)
        assert result.prefer_full_pack() is True

    def test_missing_root(self, fs):
        """A root that does not exist is an empty tree."""
        result = LocalProbe(PROBE_DECODER).presence(ROOT)
        assert result == PresenceResult(
            present_bytes=0, total_bytes=PROBE_TOTAL, missing_blocks=4, complete=True)
        assert result.prefer_full_pack() is True

    def test_shared_block_counted_once(self, fs):
        """A missing record of a shared block drops the block once, not
        per reference: the fetch of the block serves them all."""
        write_tree(PROBE_DECODER, fs, exclude=('b.txt',))
        result = LocalProbe(PROBE_DECODER).presence(ROOT)
        assert result == PresenceResult(
            present_bytes=PROBE_TOTAL - SHARED_SIZE,
            total_bytes=PROBE_TOTAL,
            missing_blocks=1,
        )

    def test_folder_at_path_is_missing(self, fs):
        """A folder at a record path holds no content of the record, the
        block is missing."""
        write_tree(PROBE_DECODER, fs, exclude=('c.txt',))
        fs.create_dir(f'{ROOT}/c.txt')
        result = LocalProbe(PROBE_DECODER).presence(ROOT)
        c_size = PROBE_DECODER.fileinfo['c.txt'].data_size
        assert result == PresenceResult(
            present_bytes=PROBE_TOTAL - c_size,
            total_bytes=PROBE_TOTAL,
            missing_blocks=1,
        )

    def test_dangling_symlink_is_missing(self, fs):
        """A symbolic link to a missing target holds no content, the block
        is missing."""
        write_tree(PROBE_DECODER, fs, exclude=('c.txt',))
        fs.create_symlink(f'{ROOT}/c.txt', 'missing-target')
        result = LocalProbe(PROBE_DECODER).presence(ROOT)
        c_size = PROBE_DECODER.fileinfo['c.txt'].data_size
        assert result == PresenceResult(
            present_bytes=PROBE_TOTAL - c_size,
            total_bytes=PROBE_TOTAL,
            missing_blocks=1,
        )

    def test_symlink_to_file_is_present(self, fs):
        """A symbolic link to a regular file is read like the file itself,
        the block counts as present."""
        write_tree(PROBE_DECODER, fs, exclude=('c.txt',))
        fs.create_symlink(f'{ROOT}/c.txt', 'a.txt')
        result = LocalProbe(PROBE_DECODER).presence(ROOT)
        assert result == PresenceResult(
            present_bytes=PROBE_TOTAL, total_bytes=PROBE_TOTAL, missing_blocks=0)

    def test_wrong_content_counts_present(self, fs):
        """The probe checks the paths only: a file of wrong content counts
        as present, the per-file validation covers the content later."""
        for path, info in PROBE_DECODER.fileinfo.items():
            if info.edit != 2:
                fs.create_file(f'{ROOT}/{path}', contents=b'wrong content')
        result = LocalProbe(PROBE_DECODER).presence(ROOT)
        assert result == PresenceResult(
            present_bytes=PROBE_TOTAL, total_bytes=PROBE_TOTAL, missing_blocks=0)

    def test_data_less_records_not_accounted(self, fs):
        """A missing empty file does not change the accounting."""
        write_tree(PROBE_DECODER, fs, exclude=('empty.txt',))
        result = LocalProbe(PROBE_DECODER).presence(ROOT)
        assert result == PresenceResult(
            present_bytes=PROBE_TOTAL, total_bytes=PROBE_TOTAL, missing_blocks=0)

    def test_reproducible(self, fs):
        """The same tree gives the same accounting twice: the decision of
        a tree is reproducible."""
        write_tree(PROBE_DECODER, fs, exclude=('b.txt',))
        probe = LocalProbe(PROBE_DECODER)
        assert probe.presence(ROOT) == probe.presence(ROOT)

    def test_index_pack_same_accounting(self, fs):
        """The accounting of the index pack decoder equals the one of the
        full pack decoder on the same tree."""
        write_tree(PROBE_DECODER, fs)
        assert LocalProbe(PROBE_INDEX_DECODER).presence(ROOT) == \
            LocalProbe(PROBE_DECODER).presence(ROOT)


class TestEarlyStop:
    """The scan checks the largest blocks first and stops as soon as the
    decision is certain."""

    def test_blocks_largest_first(self):
        """The blocks of the probe are the stored blocks by size,
        largest first."""
        probe = LocalProbe(SKEW_DECODER)
        sizes = [block.data_size for block in probe.blocks]
        assert sizes == sorted(sizes, reverse=True)
        assert probe.total_bytes == sum(sizes)

    def test_empty_tree_stops_after_the_first_wave(self, fs, monkeypatch):
        """An empty tree stops after the first wave of the scan: the hun-
        dred small records behind the large blocks are never checked."""
        calls = []
        original = os.stat

        def _counting(path, *args, **kwargs):
            calls.append(str(path))
            return original(path, *args, **kwargs)
        monkeypatch.setattr(os, 'stat', _counting)

        probe = LocalProbe(SKEW_DECODER)
        result = probe.presence(ROOT)
        assert result.present_bytes == 0
        # every checked block was missing
        assert result.missing_blocks == len(calls)
        # one wave of the scan, the blocks behind it are skipped
        assert len(calls) == local_probe.PROBE_WAVE * local_probe.PROBE_CHUNK
        assert len(calls) < len(probe.blocks)
        assert result.complete is False
        assert result.prefer_full_pack() is True

    def test_verdict_matches_the_complete_accounting(self, fs):
        """The early stop only skips work: the verdict of a partial scan
        equals the verdict of the complete accounting."""
        probe = LocalProbe(SKEW_DECODER)
        # an empty tree: the scan stops early
        result = probe.presence(ROOT)
        assert result.complete is False
        assert result.prefer_full_pack() == spec_presence(SKEW_DECODER, ROOT).prefer_full_pack()
        # a full tree: the scan reaches the end and the per-file way wins
        write_tree(SKEW_DECODER, fs)
        result = probe.presence(ROOT)
        assert result.complete is True
        assert result.prefer_full_pack() is False
        assert result.prefer_full_pack() == spec_presence(SKEW_DECODER, ROOT).prefer_full_pack()

    def test_budget_stop(self, fs, monkeypatch):
        """The scan stops as soon as the missing blocks are over the
        budget: one range request per missing block is already too many,
        the rest of the scan cannot bring them back."""
        write_tree(GUARD_DECODER, fs, exclude=GUARD_MISSING)
        monkeypatch.setattr(local_probe, 'REQUEST_BUDGET', 2)
        # one block per wave, the early stop is evaluated after every block
        monkeypatch.setattr(local_probe, 'PROBE_CHUNK', 1)
        monkeypatch.setattr(local_probe, 'PROBE_WAVE', 1)
        result = LocalProbe(GUARD_DECODER).presence(ROOT)
        assert result.present_ratio > PRESENT_RATIO
        assert result.missing_blocks == len(GUARD_MISSING)
        assert result.complete is False
        assert result.prefer_full_pack() is True

    def test_budget_met_completes_the_scan(self, fs, monkeypatch):
        """The same tree with the missing blocks inside the budget keeps
        the per-file way and the scan reaches the end: the budget is the
        only difference."""
        write_tree(GUARD_DECODER, fs, exclude=GUARD_MISSING)
        monkeypatch.setattr(local_probe, 'REQUEST_BUDGET', len(GUARD_MISSING))
        # one block per wave, the early stop is evaluated after every block
        monkeypatch.setattr(local_probe, 'PROBE_CHUNK', 1)
        monkeypatch.setattr(local_probe, 'PROBE_WAVE', 1)
        result = LocalProbe(GUARD_DECODER).presence(ROOT)
        assert result.missing_blocks == len(GUARD_MISSING)
        assert result.complete is True
        assert result.prefer_full_pack() is False

    def test_stop_on_the_last_block_is_complete(self, fs, monkeypatch):
        """A decision that becomes certain on the last block skips no
        block: the accounting is complete, not a lower bound."""
        for i in (3, 7):
            fs.create_file(f'{ROOT}/f{i}.bin', contents=LAST_FILES[f'f{i}.bin'])
        calls = []
        original = os.stat

        def _counting(path, *args, **kwargs):
            calls.append(str(path))
            return original(path, *args, **kwargs)
        monkeypatch.setattr(os, 'stat', _counting)

        probe = LocalProbe(LAST_DECODER)
        result = probe.presence(ROOT)
        # every block was checked, the ratio bound is crossed by the last
        # one of them
        assert len(calls) == len(probe.blocks)
        assert result.present_ratio < PRESENT_RATIO
        assert result.complete is True
        assert result.prefer_full_pack() is True


class TestPreferFullPack:
    """PresenceResult: the byte ratio and the request budget decide the
    way to apply the pack."""

    @pytest.mark.parametrize('present_bytes, total_bytes, missing_blocks, expected', [
        # the tree supplies every byte and no block is missing: per-file
        (106, 106, 0, False),
        # exactly PRESENT_RATIO (1/5) of the bytes: per-file, the bound
        # is "at least"
        (200, 1000, 0, False),
        # just below PRESENT_RATIO: full pack
        (199, 1000, 0, True),
        # the tree supplies nothing: full pack, however small the pack
        (0, 106, 0, True),
        # exactly REQUEST_BUDGET missing blocks: per-file, the bound is
        # "at most"
        (1000, 1000, REQUEST_BUDGET, False),
        # one missing block over the budget: full pack, however much of
        # the pack the tree supplies
        (1000, 1000, REQUEST_BUDGET + 1, True),
        # no data section: per-file trivially, nothing to fetch either way
        (0, 0, 0, False),
    ])
    def test_decision(self, present_bytes, total_bytes, missing_blocks, expected):
        """The decision is the byte ratio bound and the request budget
        bound, both inclusive on the per-file side."""
        result = PresenceResult(
            present_bytes=present_bytes,
            total_bytes=total_bytes,
            missing_blocks=missing_blocks,
        )
        assert result.prefer_full_pack() is expected

    @pytest.mark.parametrize('present_bytes, total_bytes, expected', [
        (200, 1000, 0.2),
        (0, 1000, 0.0),
        (1000, 1000, 1.0),
        # no data section, the ratio is defined as complete
        (0, 0, 1.0),
    ])
    def test_present_ratio(self, present_bytes, total_bytes, expected):
        result = PresenceResult(
            present_bytes=present_bytes, total_bytes=total_bytes, missing_blocks=0)
        assert result.present_ratio == expected

    def test_policy_constants(self):
        """The policy defaults are explicit constants, calibratable by the
        measurements of real packs."""
        assert PRESENT_RATIO == 1 / 5
        assert REQUEST_BUDGET == 100
