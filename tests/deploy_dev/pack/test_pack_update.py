"""
Tests for PackUpdate: generate update packs from an old full pack to a new full pack.

Versions are built with MockGitRepo, the update pack is built with
PackUpdate and validated on the decode side, then the update is applied
to the old working tree like the client would and the result is
compared to the new working tree (round-trip). The apply flow follows
the client design: all sources are read from the original working tree
in the unpack phase, then all changes are written in the replace phase.
"""
from hashlib import sha1

import pytest
from conftest import code_lines, damage, damage_lines, random_bytes

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy_dev.pack.pack_repo import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.ext.compress.algo_zstd import zstd_compress, zstd_decompress
from alasio.git.mock.mock_repo import MockGitRepo

# ════════════════════════════════════════════════════════════════════════════
#  helpers
# ════════════════════════════════════════════════════════════════════════════


def make_pack(files, commit='c1'):
    """
    Build a full pack of a version.

    Args:
        files (dict[str, bytes | tuple[bytes, int]]): {path: content} or
            {path: (content, mode)}
        commit (str): Version of the pack. Defaults to 'c1'.

    Returns:
        bytes: Full pack data
    """
    repo = MockGitRepo()
    for path, value in files.items():
        if isinstance(value, tuple):
            content, mode = value
        else:
            content = value
            mode = 644
        repo.register_file(commit, path, content, mode=mode)
    return b''.join(PackFull(repo, commit=commit).iter_pack_data())


def decode(data):
    """
    Decode and validate a pack.

    Args:
        data (bytes): Pack data

    Returns:
        PackDecodeBase: Decoder
    """
    decoder = PackDecodeBase(data)
    decoder.validate()
    return decoder


def unpack_tree(decoder):
    """
    Extract the working tree of a full pack as {path: content}.

    Args:
        decoder (PackDecodeBase): Decoder of the full pack

    Returns:
        dict[str, bytes]: Working tree content
    """
    return {
        path: bytes(decoder.catfile(info))
        for path, info in decoder.fileinfo.items()
        if info.edit != 2
    }


def apply_update(update_pack, old_pack, old_tree):
    """
    Apply an update pack to an old working tree, like the client.

    All sources are read from the original working tree in the unpack
    phase (records are processed in fileinfo order), then the changes
    are applied in the replace phase. refinfo entries are verified
    against the sources before use.

    Args:
        update_pack (bytes): Update pack data
        old_pack (bytes): Old full pack data, provides the old eol rules
        old_tree (dict[str, bytes]): Old working tree

    Returns:
        dict[str, bytes]: New working tree
    """
    decoder = decode(update_pack)
    old_decoder = PackDecodeBase(old_pack)
    old_eol = {path: info.eol for path, info in old_decoder.fileinfo.items() if info.edit != 2}

    def read_blob(path):
        """
        Read the LF blob of an old file, normalized by its eol.
        """
        content = old_tree[path]
        if old_eol[path] == 1:
            content = content.replace(b'\r\n', b'\n')
        ref = decoder.refinfo[path]
        assert len(content) == ref.size, f'refinfo size mismatch: {path}'
        assert sha1(content).hexdigest() == ref.sha1, f'refinfo sha1 mismatch: {path}'
        return content

    # unpack phase: compute every change from the original tree
    changes = {}
    for path, info in decoder.fileinfo.items():
        if info.edit == 2:
            # deleted
            changes[path] = None
            continue
        if info.edit == 0 and info.source_lookback:
            # copied, content of the source (an old file or an earlier new file)
            content = changes.get(info.source_path)
            if content is None:
                # the source is an unchanged old file, verify it first
                content = read_blob(info.source_path)
            changes[path] = content
            continue
        if info.edit == 3 and info.data_size == 0:
            # pure rename, move the source file
            changes[path] = PackDecodeBase.apply_eol(read_blob(info.source_path), info.eol)
            changes[info.source_path] = None
            continue
        if info.algo == 2 and info.source_lookback:
            # zstd patch from the old blob
            source = read_blob(info.source_path)
            blob = zstd_decompress(decoder.catdata(info), source=source)
            changes[path] = PackDecodeBase.apply_eol(blob, info.eol)
            if info.edit == 3:
                changes[info.source_path] = None
            continue
        # added, or modified with plain data
        changes[path] = bytes(decoder.catfile(info))
        if info.edit == 3:
            changes[info.source_path] = None

    # replace phase
    result = {path: content for path, content in old_tree.items() if path not in changes}
    for path, content in changes.items():
        if content is not None:
            result[path] = content
    return result


def build_update(old_files, new_files, **kwargs):
    """
    Build an update pack between two versions.

    Args:
        old_files (dict): Files of the old version
        new_files (dict): Files of the new version
        **kwargs: Arguments passed to PackUpdate

    Returns:
        tuple: (update pack bytes, PackUpdate, old decoder, new decoder, update decoder)
    """
    old_pack = make_pack(old_files, commit='old')
    new_pack = make_pack(new_files, commit='new')
    old_decoder = PackDecodeBase(old_pack)
    new_decoder = PackDecodeBase(new_pack)
    updater = PackUpdate(old_decoder, new_decoder, **kwargs)
    update = b''.join(updater.iter_pack_data())
    update_decoder = decode(update)
    return update, updater, old_decoder, new_decoder, update_decoder


def assert_roundtrip(old_files, new_files, **kwargs):
    """
    Build the update and verify it applies to the old working tree.

    Args:
        old_files (dict): Files of the old version
        new_files (dict): Files of the new version
        **kwargs: Arguments passed to PackUpdate

    Returns:
        tuple: (update pack bytes, PackUpdate, old decoder, new decoder, update decoder)
    """
    update, updater, old_decoder, new_decoder, update_decoder = build_update(
        old_files, new_files, **kwargs)
    old_tree = unpack_tree(old_decoder)
    new_tree = unpack_tree(new_decoder)
    result = apply_update(update, old_decoder.data, old_tree)
    assert result == new_tree
    return update, updater, old_decoder, new_decoder, update_decoder


# ════════════════════════════════════════════════════════════════════════════
#  basic diffs
# ════════════════════════════════════════════════════════════════════════════


class TestPackUpdateBasic:
    """Basic diff types: unchanged, added, deleted, modified."""

    def test_unchanged_files_absent(self):
        """Unchanged files are left out of the update pack."""
        files = {'a.txt': b'hello', 'b/b.txt': b'world'}
        update, updater, *_ = build_update(files, files)
        assert updater.diff_info == {}
        assert updater.refinfo == {}
        assert updater.fileinfo == {}
        assert_roundtrip(files, files)

    def test_version(self):
        """The update pack records the new version."""
        old = {'a.txt': b'old'}
        new = {'a.txt': b'new'}
        _, updater, old_decoder, new_decoder, update_decoder = build_update(old, new)
        assert updater.latest_commit == 'new'
        assert update_decoder.version == 'new'
        assert update_decoder.version != old_decoder.version
        assert update_decoder.version == new_decoder.version

    def test_added_file(self):
        """A new file becomes an A record with data."""
        old = {'a.txt': b'old'}
        new = {'a.txt': b'old', 'new.txt': b'brand new content\n'}
        _, updater, *_ = build_update(old, new)
        info = updater.diff_info['new.txt']
        assert info.edit == 0
        assert info.source_path == ''
        assert info.size == len(b'brand new content\n')
        assert info.data_size > 0
        assert_roundtrip(old, new)

    def test_added_empty_file(self):
        """An empty new file becomes an A record without data."""
        old = {}
        new = {'empty.txt': b''}
        _, updater, _, _, update_decoder = build_update(old, new)
        info = updater.fileinfo['empty.txt']
        assert info.edit == 0
        assert info.size == 0
        assert info.data_size == 0
        assert info.sha1 == ''
        assert_roundtrip(old, new)
        # the decoder can read the empty file
        assert bytes(update_decoder.catfile(update_decoder.fileinfo['empty.txt'])) == b''

    def test_deleted_file(self):
        """A removed file becomes a D record keyed by the old path."""
        old = {'a.txt': b'old', 'gone.txt': b'delete me'}
        new = {'a.txt': b'old'}
        _, updater, *_ = build_update(old, new)
        info = updater.diff_info['gone.txt']
        assert info.edit == 2
        assert info.source_path == ''
        assert_roundtrip(old, new)

    def test_modified_file(self):
        """A changed file becomes an M record with a zstd patch."""
        old = {'a.txt': b'version 1\n' * 100}
        new = {'a.txt': b'version 2\n' * 100}
        _, updater, old_decoder, _, update_decoder = build_update(old, new)
        info = updater.diff_info['a.txt']
        assert info.edit == 1
        assert info.source_path == 'a.txt'
        # the patch data wins and references the old file
        assert info.algo == 2
        assert info.source_lookback > 0
        assert info.data_size < info.size
        assert 'a.txt' in updater.refinfo
        ref = updater.refinfo['a.txt']
        assert ref.sha1 == old_decoder.fileinfo['a.txt'].sha1
        # the update pack decoder resolves the same records
        fileinfo = update_decoder.fileinfo
        assert fileinfo['a.txt'].edit == 1
        assert fileinfo['a.txt'].source_path == 'a.txt'
        assert fileinfo['a.txt'].sha1 == sha1(new['a.txt']).hexdigest()
        assert update_decoder.refinfo['a.txt'].sha1 == old_decoder.fileinfo['a.txt'].sha1
        assert_roundtrip(old, new)

    def test_modified_from_empty_no_refinfo(self):
        """An M record whose old file is empty stores plain data, no refinfo."""
        old = {'a.txt': b''}
        new = {'a.txt': b'hello world\n' * 50}
        _, updater, *_ = build_update(old, new)
        info = updater.fileinfo['a.txt']
        assert info.edit == 1
        assert info.source_lookback == 0
        assert updater.refinfo == {}
        assert_roundtrip(old, new)

    def test_modified_to_empty(self):
        """An M record whose new file is empty stores no data."""
        old = {'a.txt': b'hello world\n' * 50}
        new = {'a.txt': b''}
        _, updater, *_ = build_update(old, new)
        info = updater.fileinfo['a.txt']
        assert info.edit == 1
        assert info.size == 0
        assert info.data_size == 0
        assert_roundtrip(old, new)

    def test_mode_change_only(self):
        """A mode change with identical content is an M record with the new mode."""
        old = {'run.sh': (b'#!/bin/sh\necho hi\n', 755)}
        new = {'run.sh': (b'#!/bin/sh\necho hi\n', 644)}
        _, updater, *_ = build_update(old, new)
        info = updater.fileinfo['run.sh']
        assert info.edit == 1
        assert info.mode == 0
        assert_roundtrip(old, new)

    def test_eol_change_only(self):
        """An eol change with identical blob is an M record with the new eol."""
        old = {'data.txt': b'line1\nline2\n'}
        new = {
            '.gitattributes': b'*.txt text eol=crlf\n',
            'data.txt': b'line1\nline2\n',
        }
        _, updater, _, _, update_decoder = build_update(old, new)
        info = updater.fileinfo['data.txt']
        assert info.edit == 1
        assert info.eol == 1
        assert_roundtrip(old, new)
        # the applied file is CRLF
        assert update_decoder.fileinfo['data.txt'].eol == 1

    def test_folder_init_py_deleted(self):
        """Removing a folder __init__.py produces a D record."""
        old = {'pkg/__init__.py': b'', 'pkg/mod.py': b'VALUE = 1\n'}
        new = {'pkg/mod.py': b'VALUE = 1\n'}
        _, updater, *_ = build_update(old, new)
        assert updater.fileinfo['pkg/__init__.py'].edit == 2
        assert_roundtrip(old, new)

    def test_folder_init_py_added(self):
        """Adding a folder __init__.py produces an A record."""
        old = {'pkg/mod.py': b'VALUE = 1\n'}
        new = {'pkg/__init__.py': b'', 'pkg/mod.py': b'VALUE = 1\n'}
        _, updater, *_ = build_update(old, new)
        assert updater.fileinfo['pkg/__init__.py'].edit == 0
        assert updater.fileinfo['pkg/__init__.py'].size == 0
        assert_roundtrip(old, new)


# ════════════════════════════════════════════════════════════════════════════
#  renames
# ════════════════════════════════════════════════════════════════════════════


class TestPackUpdateRename:
    """Rename detection: R (pure) and RM (renamed + modified)."""

    def test_pure_rename(self):
        """Same content at a new path becomes an R record, no data."""
        old = {'a.txt': b'hello world\n' * 20}
        new = {'moved.txt': b'hello world\n' * 20}
        _, updater, _, _, update_decoder = build_update(old, new)
        info = updater.diff_info['moved.txt']
        assert info.edit == 3
        assert info.source_path == 'a.txt'
        assert info.data_size == 0
        # the old path is moved, not deleted
        assert 'a.txt' not in updater.fileinfo
        assert 'a.txt' in updater.refinfo
        assert update_decoder.fileinfo['moved.txt'].edit == 3
        assert update_decoder.fileinfo['moved.txt'].source_path == 'a.txt'
        assert_roundtrip(old, new)

    def test_rename_modify(self):
        """A renamed file with changes becomes an RM record with a patch."""
        lines = code_lines(4000)
        old = {'a.txt': b''.join(lines)}
        new = {'moved.txt': damage_lines(lines, 0.05, seed=3)}
        _, updater, *_ = build_update(old, new)
        info = updater.diff_info['moved.txt']
        assert info.edit == 3
        assert info.source_path == 'a.txt'
        assert info.algo == 2
        assert info.data_size > 0
        assert 'a.txt' in updater.refinfo
        assert_roundtrip(old, new)

    def test_unrelated_not_renamed(self):
        """Unrelated contents are not matched, the files become D and A."""
        old = {'a.txt': random_bytes(4096, 'old')}
        new = {'b.txt': random_bytes(4096, 'new')}
        _, updater, *_ = build_update(old, new)
        assert updater.fileinfo['a.txt'].edit == 2
        assert updater.fileinfo['b.txt'].edit == 0
        assert updater.fileinfo['b.txt'].source_lookback == 0
        assert updater.refinfo == {}
        assert_roundtrip(old, new)

    def test_min_similarity_threshold(self):
        """min_similarity controls whether a pair is matched as a rename."""
        old = {'a.txt': b'def func(x):\n    return x * 2\n' * 60}
        new = {'b.txt': damage(b'def func(x):\n    return x * 2\n' * 60, 0.1, seed=4)}
        # default threshold: matched as RM
        _, updater, *_ = build_update(old, new)
        assert updater.fileinfo['b.txt'].edit == 3
        assert updater.diff_info['b.txt'].source_path == 'a.txt'
        assert_roundtrip(old, new)
        # high threshold: not matched, D + A instead
        _, updater, *_ = build_update(old, new, min_similarity=0.9)
        assert updater.fileinfo['a.txt'].edit == 2
        assert updater.fileinfo['b.txt'].edit == 0
        assert_roundtrip(old, new)

    def test_one_to_one_matching(self):
        """Every old file is the source of at most one rename."""
        old = {'a.txt': b'def func(x):\n    return x * 2\n' * 60}
        new = {
            'b1.txt': damage(b'def func(x):\n    return x * 2\n' * 60, 0.05, seed=5),
            'b2.txt': damage(b'def func(x):\n    return x * 2\n' * 60, 0.05, seed=6),
        }
        _, updater, *_ = build_update(old, new)
        # exactly one of the two new files is the rename, the other is added
        renamed = [path for path, info in updater.fileinfo.items() if info.edit == 3]
        added = [path for path, info in updater.fileinfo.items() if info.edit == 0]
        assert len(renamed) == 1
        assert len(added) == 1
        assert updater.diff_info[renamed[0]].source_path == 'a.txt'
        # the old file is moved, not deleted
        assert 'a.txt' not in updater.fileinfo
        assert_roundtrip(old, new)

    def test_rename_with_eol_change(self):
        """A rename whose eol changes becomes an RM record."""
        old = {'a.txt': b'line1\nline2\n'}
        new = {
            '.gitattributes': b'*.txt text eol=crlf\n',
            'moved.txt': b'line1\nline2\n',
        }
        _, updater, _, _, update_decoder = build_update(old, new)
        info = updater.diff_info['moved.txt']
        assert info.edit == 3
        assert info.source_path == 'a.txt'
        assert info.data_size > 0
        assert info.eol == 1
        assert_roundtrip(old, new)
        # the applied file is CRLF, the record eol is 1
        assert update_decoder.fileinfo['moved.txt'].eol == 1

    def test_empty_files_not_renamed(self):
        """Empty files are never rename candidates."""
        old = {'a.txt': b''}
        new = {'b.txt': b''}
        _, updater, *_ = build_update(old, new)
        assert updater.fileinfo['a.txt'].edit == 2
        assert updater.fileinfo['b.txt'].edit == 0
        assert updater.fileinfo['b.txt'].size == 0
        assert_roundtrip(old, new)

    def test_size_filter(self):
        """Pairs with very different sizes are not rename candidates."""
        old = {'a.txt': b'x' * 1000}
        new = {'b.txt': (b'x' + b'y') * 5000}
        _, updater, *_ = build_update(old, new)
        assert updater.fileinfo['a.txt'].edit == 2
        assert updater.fileinfo['b.txt'].edit == 0
        assert_roundtrip(old, new)


# ════════════════════════════════════════════════════════════════════════════
#  copies
# ════════════════════════════════════════════════════════════════════════════


class TestPackUpdateCopied:
    """Content dedup: C records reference the source instead of carrying data."""

    def test_copy_from_unchanged_old_file(self):
        """A new file with the content of an unchanged old file is a C record."""
        old = {'keep.txt': b'copy me\n'}
        new = {'keep.txt': b'copy me\n', 'copy.txt': b'copy me\n'}
        _, updater, _, _, update_decoder = build_update(old, new)
        info = updater.diff_info['copy.txt']
        assert info.edit == 0
        assert info.source_path == 'keep.txt'
        assert info.source_lookback > 0
        assert info.data_size == 0
        # the source is kept in refinfo
        assert updater.refinfo['keep.txt'].size == len(b'copy me\n')
        # the decoder restores the meta of the copied record
        decoded = update_decoder.fileinfo['copy.txt']
        assert decoded.size == len(b'copy me\n')
        assert decoded.sha1 == sha1(b'copy me\n').hexdigest()
        assert decoded.source_path == 'keep.txt'
        assert_roundtrip(old, new)

    def test_copy_chain_new_files(self):
        """Identical new files reference the nearest earlier record."""
        content = b'shared content\n'
        old = {}
        new = {'a1.txt': content, 'a2.txt': content, 'a3.txt': content}
        _, updater, *_ = build_update(old, new)
        assert updater.fileinfo['a1.txt'].source_lookback == 0
        assert updater.diff_info['a2.txt'].source_path == 'a1.txt'
        assert updater.fileinfo['a2.txt'].source_lookback == 1
        assert updater.diff_info['a3.txt'].source_path == 'a2.txt'
        assert updater.fileinfo['a3.txt'].source_lookback == 1
        assert updater.refinfo == {}
        assert_roundtrip(old, new)

    def test_crlf_file_not_copied(self):
        """A CRLF old file cannot be a copy source, the new file is added."""
        attr = b'*.txt text eol=crlf\n'
        old = {'.gitattributes': attr, 'keep.txt': b'copy me\n'}
        new = {'.gitattributes': attr, 'keep.txt': b'copy me\n', 'copy.txt': b'copy me\n'}
        _, updater, *_ = build_update(old, new)
        info = updater.fileinfo['copy.txt']
        assert info.edit == 0
        assert info.source_lookback == 0
        assert info.data_size > 0
        assert updater.refinfo == {}
        assert_roundtrip(old, new)

    def test_755_file_not_copied(self):
        """A 755 old file cannot be a copy source, the new file is added."""
        old = {'keep.sh': (b'#!/bin/sh\n', 755)}
        new = {'keep.sh': (b'#!/bin/sh\n', 755), 'copy.sh': (b'#!/bin/sh\n', 755)}
        _, updater, *_ = build_update(old, new)
        info = updater.fileinfo['copy.sh']
        assert info.edit == 0
        assert info.source_lookback == 0
        assert updater.refinfo == {}
        assert_roundtrip(old, new)

    def test_copy_from_modified_source(self):
        """A later file can copy the new content of an earlier new file."""
        old = {}
        base = b'def shared():\n    return 1\n'
        new = {
            'first.py': base,
            'second.py': base.replace(b'1', b'2'),
            'third.py': base.replace(b'1', b'2'),
        }
        _, updater, *_ = build_update(old, new)
        assert updater.fileinfo['first.py'].source_lookback == 0
        assert updater.fileinfo['second.py'].source_lookback == 0
        assert updater.diff_info['third.py'].source_path == 'second.py'
        assert updater.fileinfo['third.py'].source_lookback == 1
        assert_roundtrip(old, new)


# ════════════════════════════════════════════════════════════════════════════
#  diff_info
# ════════════════════════════════════════════════════════════════════════════


class TestDiffInfo:
    """diff_info maps paths to IdxInfo with source_path pointing to old files."""

    def test_source_path(self):
        """Every record type carries the expected source_path."""
        old = {
            'keep.txt': b'keep\n',
            'mod.py': b'old code\n' * 5,
            'ren.py': b'rename me\n' * 5,
            'gone.txt': b'delete me\n',
        }
        new = {
            'keep.txt': b'keep\n',
            'mod.py': b'new code\n' * 5,
            'moved.py': b'rename me\n' * 5,
            'copy.txt': b'keep\n',
        }
        _, updater, *_ = build_update(old, new)
        diff = updater.diff_info
        # M: the old file of the same path
        assert diff['mod.py'].edit == 1
        assert diff['mod.py'].source_path == 'mod.py'
        # R: the rename source
        assert diff['moved.py'].edit == 3
        assert diff['moved.py'].source_path == 'ren.py'
        # C: the copied old file
        assert diff['copy.txt'].edit == 0
        assert diff['copy.txt'].source_path == 'keep.txt'
        # D: no source
        assert diff['gone.txt'].edit == 2
        assert diff['gone.txt'].source_path == ''
        # the rename source is not deleted
        assert 'ren.py' not in diff
        # every value is an IdxInfo
        assert all(isinstance(info, type(updater.diff_info['mod.py'])) for info in diff.values())
        assert_roundtrip(old, new)


# ════════════════════════════════════════════════════════════════════════════
#  index update
# ════════════════════════════════════════════════════════════════════════════


class TestPackUpdateIndexUpdate:
    """index_update patches the old index pack into the new index pack."""

    def test_index_update_roundtrip(self):
        """Decompressing with the old index pack restores the new index pack."""
        old = {'a.txt': b'old', 'same.txt': b'same\n'}
        new = {'a.txt': b'new', 'same.txt': b'same\n', 'added.txt': b'added\n'}
        _, updater, old_decoder, new_decoder, _ = build_update(old, new)
        restored = zstd_decompress(
            updater.index_update, source=old_decoder.extract_index_pack())
        assert restored == bytes(new_decoder.extract_index_pack())
        # the restored index pack is valid and has the new version
        index_decoder = PackDecodeBase(restored)
        index_decoder.validate_index()
        assert index_decoder.version == 'new'
        assert index_decoder.refinfo == {}
        assert len(index_decoder.fileinfo) == 3

    def test_index_update_small(self):
        """A small version change produces a small index patch."""
        old = {'a.txt': b'old', 'b.txt': b'keep\n'}
        new = {'a.txt': b'new', 'b.txt': b'keep\n'}
        _, updater, old_decoder, new_decoder, _ = build_update(old, new)
        patch = updater.index_update
        new_index = bytes(new_decoder.extract_index_pack())
        # the patch is much smaller than the plain compression of the new index
        assert len(patch) < len(zstd_compress(new_index, level=22)) * 0.8


# ════════════════════════════════════════════════════════════════════════════
#  input validation
# ════════════════════════════════════════════════════════════════════════════


class TestPackUpdateValidation:
    """Input validation of PackUpdate."""

    def test_index_pack_rejected(self):
        """An index pack has no data section and is rejected."""
        old_files = {'a.txt': b'old'}
        old_pack = make_pack(old_files)
        index_pack = PackDecodeBase(old_pack).extract_index_pack()
        new_decoder = PackDecodeBase(make_pack(old_files))
        with pytest.raises(ValueError, match='full packs'):
            PackUpdate(PackDecodeBase(index_pack), new_decoder)

    def test_update_pack_rejected(self):
        """An update pack as input is rejected."""
        old = {'a.txt': b'old'}
        new = {'a.txt': b'new'}
        update, _, _, new_decoder, _ = build_update(old, new)
        with pytest.raises(ValueError, match='full packs'):
            PackUpdate(PackDecodeBase(update), new_decoder)

    def test_invalid_parameters(self):
        """Out of range parameters are rejected."""
        old = {'a.txt': b'old'}
        new = {'a.txt': b'new'}
        old_decoder = PackDecodeBase(make_pack(old))
        new_decoder = PackDecodeBase(make_pack(new))
        with pytest.raises(ValueError, match='min_similarity'):
            PackUpdate(old_decoder, new_decoder, min_similarity=1.0)
        with pytest.raises(ValueError, match='min_similarity'):
            PackUpdate(old_decoder, new_decoder, min_similarity=-0.1)
        with pytest.raises(ValueError, match='max_size_ratio'):
            PackUpdate(old_decoder, new_decoder, max_size_ratio=0.5)


# ════════════════════════════════════════════════════════════════════════════
#  empty repos
# ════════════════════════════════════════════════════════════════════════════


class TestPackUpdateEmpty:
    """Empty and one-sided versions."""

    def test_empty_to_empty(self):
        """Two empty versions produce an empty update pack."""
        old = {}
        new = {}
        update, updater, old_decoder, new_decoder, update_decoder = build_update(old, new)
        assert updater.fileinfo == {}
        assert updater.refinfo == {}
        assert len(update_decoder.data) > 0
        # the index update still works
        restored = zstd_decompress(
            updater.index_update, source=old_decoder.extract_index_pack())
        assert restored == bytes(new_decoder.extract_index_pack())
        assert_roundtrip(old, new)

    def test_empty_to_files(self):
        """All files of an empty version are added."""
        old = {}
        new = {'a.txt': b'hello', 'b/b.txt': b'world'}
        _, updater, *_ = build_update(old, new)
        assert all(info.edit == 0 for info in updater.fileinfo.values())
        assert updater.refinfo == {}
        assert_roundtrip(old, new)

    def test_files_to_empty(self):
        """All files of the old version are deleted."""
        old = {'a.txt': b'hello', 'b/b.txt': b'world'}
        new = {}
        _, updater, *_ = build_update(old, new)
        assert all(info.edit == 2 for info in updater.fileinfo.values())
        assert updater.refinfo == {}
        assert_roundtrip(old, new)


# ════════════════════════════════════════════════════════════════════════════
#  ordering conventions
# ════════════════════════════════════════════════════════════════════════════


class TestPackUpdateOrdering:
    """Ordering conventions shared with the client."""

    def test_refinfo_order(self):
        """refinfo follows the old pack decode order."""
        old = {
            'zzz.txt': b'z' * 200,
            'aaa.txt': b'a' * 200,
            'mmm.txt': b'm' * 200,
            'src_old.txt': b'rename me\n',
        }
        new = {
            'zzz.txt': b'z' * 199 + b'!',
            'aaa.txt': b'a' * 199 + b'!',
            'mmm.txt': b'm' * 199 + b'!',
            'src_new.txt': b'rename me\n',
        }
        _, updater, old_decoder, _, update_decoder = build_update(old, new)
        old_order = [info.path for info in old_decoder.idx_info if info.edit != 2]
        ref_order = [path for path in old_order if path in updater.refinfo]
        assert list(updater.refinfo) == ref_order
        # the decoded update pack keeps the same order
        assert list(update_decoder.refinfo) == ref_order
        assert 'src_old.txt' in updater.refinfo
        assert 'zzz.txt' in updater.refinfo
        assert 'aaa.txt' in updater.refinfo
        assert_roundtrip(old, new)

    def test_fileinfo_sort_order(self):
        """fileinfo is sorted by parent path, then depth, then path."""
        old = {'a.txt': b'old'}
        new = {
            'a.txt': b'old',
            'deep/x.txt': b'new deep\n',
            'x.txt': b'new root\n',
            'b/b.txt': b'new b\n',
            'a/deep/x.txt': b'new a deep\n',
        }
        _, updater, _, _, update_decoder = build_update(old, new)
        paths = list(updater.fileinfo)

        def sort_key(path):
            parts = tuple(path.split('/'))
            return parts[:-1], len(parts), parts

        assert paths == sorted(paths, key=sort_key)
        assert list(update_decoder.fileinfo) == paths
        assert_roundtrip(old, new)


# ════════════════════════════════════════════════════════════════════════════
#  integration
# ════════════════════════════════════════════════════════════════════════════


class TestPackUpdateIntegration:
    """A realistic project upgrade covering every record type at once."""

    OLD = {
        '.gitattributes': b'*.py text eol=lf\n*.txt text eol=crlf\n*.sh text eol=lf\n',
        'README.md': b'# Old Project\n',
        'src/main.py': b'def main():\n    print("old")\n',
        'src/utils.py': b'def util():\n    return 1\n',
        'src/old_name.py': b'def old_name():\n    return "old"\n',
        'src/deleted.py': b''.join(code_lines(400)),
        'scripts/run.sh': (b'#!/bin/sh\necho run\n', 755),
        'scripts/run.bat': b'@echo off\r\necho run\r\n',
        'docs/notes.txt': b'old note\r\n',
        'config.json': b'{"version": 1}\n',
        'data/blob.bin': bytes(range(256)) * 20,
        'pkg/__init__.py': b'',
        'pkg/mod.py': b'VALUE = 1\n',
        'tools/tool.sh': (b'#!/bin/sh\necho tool\n', 644),
    }

    NEW = {
        '.gitattributes': b'*.py text eol=lf\n*.txt text eol=crlf\n*.sh text eol=lf\n',
        'README.md': b'# Old Project\n',
        'src/main.py': b'def main():\n    print("new")\n',
        'src/utils.py': b'def util():\n    return 1\n',
        'src/new_name.py': b'def old_name():\n    return "old"\n',
        'src/renamed_modified.py': damage_lines(code_lines(400), 0.05, seed=3),
        'scripts/run.sh': (b'#!/bin/sh\necho run\n', 755),
        'scripts/run.bat': b'@echo off\r\necho run\r\n',
        'docs/notes.txt': b'updated note\r\n',
        'config.json': b'{"version": 2}\n',
        'data/blob.bin': bytes(range(256)) * 20,
        'data/new_blob.bin': bytes(range(128)) * 40,
        'pkg/mod.py': b'VALUE = 2\n',
        'src/added.py': b'def new_func():\n    return 3\n',
        'docs/readme_copy.md': b'# Old Project\n',
        'tools/tool.sh': (b'#!/bin/sh\necho tool\n', 755),
    }

    def test_full_scenario_roundtrip(self):
        """The update applies to the old working tree and produces the new one."""
        old = self.OLD
        new = self.NEW
        _, updater, *_ = assert_roundtrip(old, new)
        # expected record types
        fileinfo = updater.fileinfo
        diff = updater.diff_info
        assert fileinfo['src/main.py'].edit == 1
        assert fileinfo['src/new_name.py'].edit == 3
        assert diff['src/new_name.py'].source_path == 'src/old_name.py'
        assert fileinfo['src/renamed_modified.py'].edit == 3
        assert diff['src/renamed_modified.py'].source_path == 'src/deleted.py'
        assert fileinfo['pkg/__init__.py'].edit == 2
        assert fileinfo['docs/readme_copy.md'].edit == 0
        assert diff['docs/readme_copy.md'].source_path == 'README.md'
        assert fileinfo['tools/tool.sh'].edit == 1
        assert fileinfo['tools/tool.sh'].mode == 1
        assert fileinfo['src/added.py'].edit == 0
        # unchanged files are absent
        for path in ('README.md', 'src/utils.py', 'scripts/run.sh', 'scripts/run.bat',
                     'data/blob.bin'):
            assert path not in fileinfo

    def test_full_scenario_decoder_records(self):
        """The decoded update pack is self-consistent."""
        old = self.OLD
        new = self.NEW
        _, _, _, _, update_decoder = build_update(old, new)
        # every source_path resolves inside the merged record list
        # (an M record shares its path with the old record in refinfo)
        merged = list(update_decoder.refinfo) + list(update_decoder.fileinfo)
        for info in update_decoder.fileinfo.values():
            if info.source_path:
                assert info.source_path in merged, f'broken source: {info.path}'
        # M records with patch data reference the old record of the same path
        for info in update_decoder.fileinfo.values():
            if info.edit == 1 and info.source_lookback:
                assert info.source_path == info.path
                assert info.path in update_decoder.refinfo
        # R / RM records reference the rename source in refinfo
        for info in update_decoder.fileinfo.values():
            if info.edit == 3:
                assert info.source_path in update_decoder.refinfo

    def test_update_pack_smaller_than_full_pack(self):
        """The update pack is much smaller than the new full pack."""
        old = self.OLD
        new = self.NEW
        update, _, _, _, _ = build_update(old, new)
        # the new full pack is built directly from the same files
        full = make_pack(new, commit='new')
        assert len(update) < len(full) * 0.8
