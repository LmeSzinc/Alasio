"""
Tests for PackDecodeManifest: manifest decode and validation.

Uses PackEncodeManifest (alasio.deploy_dev) to encode a manifest of a mock
frontend build, then verifies the decode side restores every record: the
filepath, the size and the sha1 of every file, plus the trailing checksum.

The malformed cases are assembled with build_manifest(), the low level
encoders PackEncodeManifest itself uses: the checksum always matches the bytes,
so validate() passes and only the records are broken. A matching checksum
proves the manifest was not modified after it was encoded, it does not
prove the records are usable, the decode side must reject them too.
"""
from hashlib import sha1

import pytest

from alasio.deploy.pack.decode_base import PackDecodeError
from alasio.deploy.pack.decode_manifest import PackDecodeManifest
from alasio.deploy.pack.pack_model import RefInfo
from alasio.deploy_dev.pack.encode_manifest import PackEncodeManifest
from alasio.ext.algorithm.pathcomb import iter_path_comb
from alasio.ext.algorithm.pathlen_coding import (
    decode_prefix_comb, decode_suffix_comb, encode_prefix_comb, encode_suffix_comb
)
from alasio.ext.algorithm.vlenint import decode_vlenint, encode_vlenint

# ════════════════════════════════════════════════════════════════════════════
#  test data
# ════════════════════════════════════════════════════════════════════════════

# The frontend build of a mock svelte app, the file list a wheel ships in
# alasio/deploy_data/frontend. The long shared prefix and the shared file
# extensions cover the path reuse of the manifest, the empty file covers
# the zero size, favicon.ico covers a binary content.
FRONTEND_FILES = {
    'alasio/deploy_data/frontend/index.html': (
        b'<!doctype html>\n'
        b'<html lang="en">\n'
        b'  <head>\n'
        b'    <meta charset="utf-8" />\n'
        b'    <title>Alasio</title>\n'
        b'    <script type="module" src="/assets/index-a1b2c3d4.js"></script>\n'
        b'    <link rel="stylesheet" href="/assets/index-a1b2c3d4.css" />\n'
        b'  </head>\n'
        b'  <body>\n'
        b'    <div id="app"></div>\n'
        b'  </body>\n'
        b'</html>\n'
    ),
    'alasio/deploy_data/frontend/assets/index-a1b2c3d4.js':
        b'import { mount } from "svelte";\n' * 20,
    'alasio/deploy_data/frontend/assets/index-a1b2c3d4.css':
        b'.app { display: flex; }\n' * 10,
    'alasio/deploy_data/frontend/assets/chunk-b2c3d4e5.js':
        b'export const chunk = 1;\n' * 15,
    'alasio/deploy_data/frontend/assets/vendor-c3d4e5f6.js':
        b'export const vendor = 2;\n' * 12,
    'alasio/deploy_data/frontend/assets/logo-d4e5f6a7.svg': (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">\n'
        b'  <circle cx="16" cy="16" r="12" />\n'
        b'</svg>\n'
    ),
    'alasio/deploy_data/frontend/favicon.ico': bytes(range(256)) * 2,
    'alasio/deploy_data/frontend/robots.txt': b'User-agent: *\nDisallow:\n',
    'alasio/deploy_data/frontend/empty.txt': b'',
}

# sha1 of the empty content, the same digest every implementation computes
EMPTY_SHA1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'


# ════════════════════════════════════════════════════════════════════════════
#  helpers
# ════════════════════════════════════════════════════════════════════════════


def encode_manifest(files, version=None):
    """
    Encode a manifest of files with PackEncodeManifest.

    Args:
        files (dict[str, bytes]): {path: content}
        version (bytes, optional): Manifest version to write to the
            header. Defaults to None, the encoder default version.

    Returns:
        bytes: Manifest bytes
    """
    manifest = PackEncodeManifest()
    if version is not None:
        manifest.manifest_version = version
    for path, content in files.items():
        manifest.add_file(path, content)
    return b''.join(manifest.iter_manifest_data())


def records_of(files):
    """
    Split a {path: content} dict into the record lists of a manifest.

    Args:
        files (dict[str, bytes]): {path: content}

    Returns:
        tuple[list[str], list[int], list[str]]: (paths, sizes, sha1s)
    """
    return (
        list(files),
        [len(content) for content in files.values()],
        [sha1(content).hexdigest() for content in files.values()],
    )


def decode_combs(data):
    """
    Decode the path combs of an encoded manifest.

    Args:
        data (bytes): Manifest bytes

    Returns:
        tuple[list[int], list[int], list[int], list[int]]:
            (prefix_reuse, path_len, suffix_reuse, suffix_lookback)
    """
    data = memoryview(data)
    offset = 5
    prefix_comb, read = decode_vlenint(data[offset:])
    offset += read
    prefix_reuse, path_len = decode_prefix_comb(prefix_comb)
    suffix_comb, read = decode_vlenint(data[offset:])
    offset += read
    suffix_reuse, suffix_lookback = decode_suffix_comb(suffix_comb)
    return prefix_reuse, path_len, suffix_reuse, suffix_lookback


def build_manifest(paths, sizes, sha1s, prefix_comb=None, suffix_comb=None):
    """
    Build a manifest from raw sections, with a matching checksum.

    PackEncodeManifest encodes a consistent manifest from a {path: content}
    dict. This builder assembles the sections directly, so the malformed
    manifests can be built as well: the checksum always matches the
    bytes, only the sections are inconsistent.

    Args:
        paths (list[str]): Full paths in the encoded order. Duplicate
            and unsafe paths are allowed, PackEncodeManifest rejects both.
        sizes (list[int]): Size of every record
        sha1s (list[str]): 40 chars hex digest of every record, written
            as the raw 20 bytes digest like the encoder does
        prefix_comb (list[int], optional): Override the prefix comb.
            Defaults to None, encoded from the paths.
        suffix_comb (list[int], optional): Override the suffix comb.
            Defaults to None, encoded from the paths.

    Returns:
        bytes: Manifest bytes
    """
    list_path = []
    list_prefix_reuse = []
    list_suffix_reuse = []
    list_suffix_lookback = []
    for prefix_reuse, path, suffix_reuse, suffix_lookback in iter_path_comb(paths):
        list_path.append(path.encode())
        list_prefix_reuse.append(prefix_reuse)
        list_suffix_reuse.append(suffix_reuse)
        list_suffix_lookback.append(suffix_lookback)

    if prefix_comb is None:
        prefix_comb = encode_prefix_comb(list_prefix_reuse, [len(path) for path in list_path])
    if suffix_comb is None:
        suffix_comb = encode_suffix_comb(list_suffix_reuse, list_suffix_lookback)

    data = b''.join([
        b'MANI',
        b'\x00',
        encode_vlenint(prefix_comb),
        encode_vlenint(suffix_comb),
        b''.join(list_path),
        encode_vlenint(sizes),
        b''.join(bytes.fromhex(digest) for digest in sha1s),
    ])
    return data + sha1(data).digest()


def assert_truncations_fail(data):
    """
    Every truncation of data must raise PackDecodeError.

    Both validate() and the full decode path (files) must raise
    PackDecodeError for any end in range(len(data)), never another
    exception and never succeed silently.

    Args:
        data (bytes): Intact manifest to truncate
    """
    data = memoryview(data)
    for end in range(len(data)):
        truncated = data[:end]
        try:
            PackDecodeManifest(truncated).validate()
        except PackDecodeError:
            pass
        except Exception as e:
            raise AssertionError(
                f'truncate at {end}: validate raised {type(e).__name__}: {e}'
            ) from e
        else:
            raise AssertionError(f'truncate at {end}: validate did not raise')
        try:
            _ = PackDecodeManifest(truncated).files
        except PackDecodeError:
            pass
        except Exception as e:
            raise AssertionError(
                f'truncate at {end}: decode raised {type(e).__name__}: {e}'
            ) from e
        else:
            raise AssertionError(f'truncate at {end}: decode did not raise')


# Module level singleton shared by all tests: the encoded manifest of the
# mock frontend build is read-only test data. A test that needs a tampered
# manifest builds its own with build_manifest().
MANIFEST_DATA = encode_manifest(FRONTEND_FILES)


# ════════════════════════════════════════════════════════════════════════════
#  basic structure
# ════════════════════════════════════════════════════════════════════════════


class TestManifestDecodeBasic:
    """Basic structure decode."""

    def test_header(self):
        """Header magic and manifest version must be decoded."""
        decoder = PackDecodeManifest(MANIFEST_DATA)
        assert decoder.manifest_version == b'\x00'

    def test_data_is_memoryview(self):
        """The data must be exposed as a memoryview of the input."""
        decoder = PackDecodeManifest(bytearray(MANIFEST_DATA))
        assert isinstance(decoder.data, memoryview)
        assert bytes(decoder.data) == MANIFEST_DATA

    def test_validate_passes(self):
        """A well-formed manifest must validate."""
        PackDecodeManifest(MANIFEST_DATA).validate()  # must not raise

    def test_checksum(self):
        """checksum is the trailing 20 bytes digest of the manifest, the
        same value validate() verifies."""
        decoder = PackDecodeManifest(MANIFEST_DATA)
        assert decoder.checksum == sha1(MANIFEST_DATA[:-20]).hexdigest()
        assert len(decoder.checksum) == 40
        decoder.validate()

    def test_files_is_refinfo_dict(self):
        """files must be a {filepath: RefInfo} dict."""
        files = PackDecodeManifest(MANIFEST_DATA).files
        assert isinstance(files, dict)
        assert list(files) == list(FRONTEND_FILES)
        assert all(isinstance(info, RefInfo) for info in files.values())


class TestManifestDecodeCached:
    """files must be computed lazily and cached."""

    def test_lazy(self):
        """Constructing the decoder must not decode the records."""
        decoder = PackDecodeManifest(MANIFEST_DATA)
        assert '_data_end' in decoder.__dict__
        assert 'files' not in decoder.__dict__

    def test_cached(self):
        """Repeated access must return the same dict object."""
        decoder = PackDecodeManifest(MANIFEST_DATA)
        first = decoder.files
        assert decoder.files is first


# ════════════════════════════════════════════════════════════════════════════
#  roundtrip
# ════════════════════════════════════════════════════════════════════════════


class TestManifestRoundtrip:
    """Decoded records must match the files the encoder was given."""

    def test_every_record(self):
        """Every record must decode to the path, size and sha1 of the file."""
        decoder = PackDecodeManifest(MANIFEST_DATA)
        assert decoder.files == {
            path: RefInfo(path=path, size=len(content), sha1=sha1(content).hexdigest())
            for path, content in FRONTEND_FILES.items()
        }

    def test_known_records(self):
        """Records must decode to the hard-coded expected RefInfo."""
        files = PackDecodeManifest(MANIFEST_DATA).files
        assert files['alasio/deploy_data/frontend/index.html'] == RefInfo(
            path='alasio/deploy_data/frontend/index.html',
            size=291,
            sha1='33a1280773941b718cfd8ae942ce2acfd9cfde47',
        )
        assert files['alasio/deploy_data/frontend/empty.txt'] == RefInfo(
            path='alasio/deploy_data/frontend/empty.txt',
            size=0,
            sha1=EMPTY_SHA1,
        )

    def test_sha1_section_is_raw_digest(self):
        """The sha1 of a record must be stored as the 20 bytes digest."""
        content = b'hello'
        data = encode_manifest({'a.txt': content})
        # the sha1 section is the last data section, right before the
        # trailing 20 bytes checksum
        section = data[-40:-20]
        assert section == sha1(content).digest()
        assert section != sha1(content).hexdigest().encode()
        # the records still hand out the hex digest
        assert PackDecodeManifest(data).files['a.txt'].sha1 == sha1(content).hexdigest()

    def test_empty_manifest(self):
        """A manifest without files must decode to no record."""
        data = encode_manifest({})
        # header + 3 empty sections (prefix comb, suffix comb, size)
        # + the trailing checksum
        assert len(data) == 5 + 1 + 1 + 1 + 20
        decoder = PackDecodeManifest(data)
        decoder.validate()
        assert decoder.files == {}

    def test_single_file(self):
        """A manifest with one file must decode."""
        files = {'a.txt': b'hello'}
        decoder = PackDecodeManifest(encode_manifest(files))
        decoder.validate()
        assert decoder.files == {
            'a.txt': RefInfo(path='a.txt', size=5, sha1=sha1(b'hello').hexdigest()),
        }

    def test_many_files(self):
        """Every file of a longer list must decode in the encoded order."""
        files = {f'folder/sub/file-{i:03d}.txt': str(i).encode() for i in range(100)}
        decoder = PackDecodeManifest(encode_manifest(files))
        decoder.validate()
        assert list(decoder.files) == list(files)
        assert decoder.files == {
            path: RefInfo(path=path, size=len(content), sha1=sha1(content).hexdigest())
            for path, content in files.items()
        }


class TestManifestSizeEncoding:
    """Size values of every vlenint byte length must decode."""

    @pytest.mark.parametrize('size', [0, 1, 255, 256, 65535, 65536])
    def test_size(self, size):
        """A file of any size must decode to its length."""
        files = {'big.bin': b'x' * size}
        info = PackDecodeManifest(encode_manifest(files)).files['big.bin']
        assert info.size == size
        assert info.sha1 == sha1(b'x' * size).hexdigest()

    def test_mixed_sizes(self):
        """Files of different sizes must decode in the encoded order."""
        files = {
            'empty.bin': b'',
            'small.bin': b'x',
            'medium.bin': b'x' * 256,
            'large.bin': b'x' * 65536,
        }
        decoder = PackDecodeManifest(encode_manifest(files))
        decoder.validate()
        assert [info.size for info in decoder.files.values()] == [0, 1, 256, 65536]


class TestManifestPathEncoding:
    """Path reuse and non ascii paths must decode."""

    def test_reuse_covered(self):
        """The frontend test data must exercise prefix and suffix reuse."""
        prefix_reuse, _, _, suffix_lookback = decode_combs(MANIFEST_DATA)
        assert any(prefix_reuse), 'the test data must reuse a prefix'
        assert any(suffix_lookback), 'the test data must reuse a suffix'
        assert all(lookback <= index for index, lookback in enumerate(suffix_lookback)), (
            'a suffix lookback must point at a previous path'
        )

    def test_fully_reused_path(self):
        """A path covered by the reused prefix and suffix carries no bytes."""
        files = {'dir/file.js': b'1', 'dir/le.js': b'2'}
        data = encode_manifest(files)
        _, path_len, _, suffix_lookback = decode_combs(data)
        # 'dir/le.js' reuses 'dir/' from the previous path and 'le.js' from
        # it, so it is fully covered and stores no remaining path byte
        assert path_len == [11, 0]
        assert suffix_lookback == [0, 1]
        assert list(PackDecodeManifest(data).files) == list(files)

    def test_prefix_reuse_compresses(self):
        """A shared prefix must be stored once, not once per path."""
        files = {f'a/very/long/shared/prefix/file-{i}.txt': b'x' for i in range(10)}
        data = encode_manifest(files)
        prefix_reuse, path_len, _, _ = decode_combs(data)
        assert list(PackDecodeManifest(data).files) == list(files)
        # every path after the first reuses the prefix of the previous one
        assert all(reuse >= len('a/very/long/shared/prefix/') for reuse in prefix_reuse[1:])
        # only the file name is stored per record, not the whole path
        total_path = sum(len(path.encode()) for path in files)
        assert sum(path_len) < total_path // 2

    def test_deep_path(self):
        """A long deep path must decode."""
        path = '/'.join(f'level{i}' for i in range(50)) + '/file.txt'
        files = {path: b'deep'}
        assert list(PackDecodeManifest(encode_manifest(files)).files) == [path]

    def test_unicode_path(self):
        """A path outside ascii must decode, it is stored as utf-8."""
        files = {
            'frontend/中文/文件.js': b'x',
            'frontend/日本語/ファイル.js': b'y',
        }
        data = encode_manifest(files)
        decoder = PackDecodeManifest(data)
        decoder.validate()
        assert list(decoder.files) == list(files)
        assert decoder.files['frontend/中文/文件.js'].size == 1

    def test_duplicate_path_rejected(self):
        """Two records sharing a path must raise instead of overwriting."""
        paths, sizes, sha1s = records_of(FRONTEND_FILES)
        # PackEncodeManifest keys the records by path, a duplicate can only be
        # encoded by the low level builder
        data = build_manifest(paths + [paths[0]], sizes + [sizes[0]], sha1s + [sha1s[0]])
        decoder = PackDecodeManifest(data)
        decoder.validate()
        with pytest.raises(PackDecodeError, match='duplicate path'):
            _ = decoder.files


# ════════════════════════════════════════════════════════════════════════════
#  checksum validation
# ════════════════════════════════════════════════════════════════════════════


class TestManifestValidate:
    """The checksum must reject tampered manifests."""

    def test_payload_tampered(self):
        """Modifying a data byte must fail validation."""
        data = bytearray(MANIFEST_DATA)
        data[10] ^= 0xFF  # inside the path combs, the structure stays parseable
        with pytest.raises(PackDecodeError, match='checksum mismatch'):
            PackDecodeManifest(data).validate()

    def test_checksum_tampered(self):
        """Modifying the trailing digest must fail validation."""
        data = bytearray(MANIFEST_DATA)
        data[-1] ^= 0xFF
        with pytest.raises(PackDecodeError, match='checksum mismatch'):
            PackDecodeManifest(data).validate()

    def test_appended_byte(self):
        """An extra trailing byte must fail validation."""
        with pytest.raises(PackDecodeError, match='checksum mismatch'):
            PackDecodeManifest(MANIFEST_DATA + b'\x00').validate()

    def test_custom_manifest_version(self):
        """The encoder writes self.manifest_version, a version the decoder
        does not know is handed out as-is, only the caller decides on it."""
        data = encode_manifest(FRONTEND_FILES, version=b'\x02')
        assert data[:5] == b'MANI\x02'

        decoder = PackDecodeManifest(data)
        assert decoder.manifest_version == b'\x02'
        # an unknown version must not break the checksum or the records
        decoder.validate()
        assert list(decoder.files) == list(FRONTEND_FILES)

    def test_default_manifest_version(self):
        """A manifest written without a version override is b'\\x00'."""
        data = encode_manifest({'a.txt': b'x'})
        assert data[:5] == b'MANI\x00'
        assert PackDecodeManifest(data).manifest_version == b'\x00'


# ════════════════════════════════════════════════════════════════════════════
#  malformed data section
# ════════════════════════════════════════════════════════════════════════════


class TestManifestMalformed:
    """A checksum-correct manifest with an inconsistent data section must
    fail to decode: the checksum proves the bytes are the encoded ones,
    the decode side still has to reject records that cannot be used."""

    @staticmethod
    def _assert_rejected(data, match):
        """
        The manifest must validate, then fail to decode the records.

        Args:
            data (bytes): Manifest bytes with a matching checksum
            match (str): Expected PackDecodeError message
        """
        decoder = PackDecodeManifest(data)
        decoder.validate()  # the checksum matches, it must not raise
        with pytest.raises(PackDecodeError, match=match):
            _ = decoder.files

    def test_suffix_count_mismatch(self):
        """A suffix comb with more values than the prefix comb must raise."""
        paths, sizes, sha1s = records_of(FRONTEND_FILES)
        count = len(paths)
        suffix_comb = encode_suffix_comb([0] * (count + 1), [0] * (count + 1))
        data = build_manifest(paths, sizes, sha1s, suffix_comb=suffix_comb)
        self._assert_rejected(data, 'path suffix comb')

    def test_prefix_count_mismatch(self):
        """A prefix comb with more values than the suffix comb must raise."""
        paths, sizes, sha1s = records_of(FRONTEND_FILES)
        count = len(paths)
        prefix_comb = encode_prefix_comb([0] * (count + 1), [0] * (count + 1))
        data = build_manifest(paths, sizes, sha1s, prefix_comb=prefix_comb)
        self._assert_rejected(data, 'path suffix comb')

    def test_size_count_mismatch(self):
        """A size section with more values than the records must raise."""
        paths, sizes, sha1s = records_of(FRONTEND_FILES)
        data = build_manifest(paths, sizes + [0], sha1s)
        self._assert_rejected(data, 'manifest: size')

    def test_size_count_missing(self):
        """A size section with fewer values than the records must raise."""
        paths, sizes, sha1s = records_of(FRONTEND_FILES)
        data = build_manifest(paths, sizes[:-1], sha1s)
        self._assert_rejected(data, 'manifest: size')

    def test_sha1_section_shorter(self):
        """A sha1 section shorter than 20 bytes per record must raise."""
        paths, sizes, sha1s = records_of(FRONTEND_FILES)
        data = build_manifest(paths, sizes, sha1s[:-1])
        self._assert_rejected(data, 'sha1 out of range')

    def test_sha1_section_longer(self):
        """A sha1 section longer than 20 bytes per record must raise."""
        paths, sizes, sha1s = records_of(FRONTEND_FILES)
        data = build_manifest(paths, sizes, sha1s + ['0' * 40])
        self._assert_rejected(data, 'sha1 out of range')

    def test_path_bytes_out_of_range(self):
        """A path comb declaring more bytes than the data section holds
        must raise."""
        paths, sizes, sha1s = records_of(FRONTEND_FILES)
        # every record declares 4096 remaining path bytes, far more than
        # the whole manifest holds
        prefix_comb = encode_prefix_comb([0] * len(paths), [4096] * len(paths))
        data = build_manifest(paths, sizes, sha1s, prefix_comb=prefix_comb)
        self._assert_rejected(data, 'path bytes out of range')

    def test_unsafe_path(self):
        """A manifest carrying an unsafe path must fail to decode."""
        paths, sizes, sha1s = records_of(FRONTEND_FILES)
        data = build_manifest(['../evil.txt'] + paths[1:], sizes, sha1s)
        self._assert_rejected(data, 'Failed to decode paths')

    @pytest.mark.parametrize('path', ['/etc/passwd', 'a/CON', 'a/b.txt '])
    def test_unsafe_path_variants(self, path):
        """Absolute, reserved and trailing space paths must be rejected."""
        data = build_manifest([path], [1], [sha1(b'x').hexdigest()])
        self._assert_rejected(data, 'Failed to decode paths')

    def test_empty_path(self):
        """An empty path must be rejected."""
        data = build_manifest([''], [1], [sha1(b'x').hexdigest()])
        self._assert_rejected(data, 'Failed to decode paths')


# ════════════════════════════════════════════════════════════════════════════
#  malformed structure
# ════════════════════════════════════════════════════════════════════════════


class TestManifestDecodeError:
    """Structural errors during construction."""

    def test_not_a_manifest(self):
        """Invalid magic must raise PackDecodeError."""
        with pytest.raises(PackDecodeError, match='header'):
            PackDecodeManifest(b'XXXXgarbage')

    @pytest.mark.parametrize('data', [b'', b'M', b'MAN', b'MANI'])
    def test_magic_truncated(self, data):
        """A manifest shorter than the magic and the version must raise."""
        with pytest.raises(PackDecodeError, match='header'):
            PackDecodeManifest(data)

    @pytest.mark.parametrize('length', [5, 6, 20, 24])
    def test_too_short_for_checksum(self, length):
        """A manifest too short to carry the checksum must raise."""
        with pytest.raises(PackDecodeError, match='truncated'):
            PackDecodeManifest(b'MANI\x00' + b'\x00' * (length - 5))

    def test_no_data_section(self):
        """A manifest with the header and the checksum only must raise."""
        payload = b'MANI\x00'
        decoder = PackDecodeManifest(payload + sha1(payload).digest())
        decoder.validate()  # the checksum matches, it must not raise
        with pytest.raises(PackDecodeError, match='path prefix comb'):
            _ = decoder.files


class TestManifestTruncate:
    """Truncation at any byte must fail with PackDecodeError, never with
    another exception and never succeed silently."""

    def test_intact(self):
        """An intact manifest must validate and decode every record."""
        decoder = PackDecodeManifest(MANIFEST_DATA)
        decoder.validate()
        assert len(decoder.files) == len(FRONTEND_FILES)

    def test_any_truncation(self):
        """Every truncation of the manifest must raise PackDecodeError."""
        assert_truncations_fail(MANIFEST_DATA)

    def test_any_truncation_empty_manifest(self):
        """Every truncation of an empty manifest must raise PackDecodeError."""
        assert_truncations_fail(encode_manifest({}))
