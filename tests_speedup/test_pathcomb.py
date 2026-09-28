"""
Tests for the C path combination encoder of ``alasio_speedup.pathcomb``.

``encode_path_comb()`` is the filepath section of a pack index: the prefix
and the suffix reuse of every path, combined, and the remaining path bytes.
The C encoder is not a second format like the bit2 one, it is the same
encoder, so it must emit exactly what the Python pipeline emits, byte for
byte: every case here compares its three sections against iter_path_comb()
plus encode_prefix_comb() and encode_suffix_comb(), which stay the
reference, and the decoder of the pack reads the result back to the paths
of the input.

The accelerator is optional: the tests skip when the shared library is
missing and cannot be built, a machine without a C compiler still runs the
rest of the test suite.
"""
import random

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.ext.algorithm.pathcomb.pathcomb_decode import decode_prefix_comb, decode_suffix_comb
from alasio.ext.algorithm.pathcomb.pathcomb_encode_c import PARAMS
from alasio.ext.algorithm.pathcomb.pathcomb_encode_python import (
    MAX_PATH_LEN, MAX_PREFIX_REUSE, MAX_SUFFIX_LOOKBACK, MAX_SUFFIX_REUSE, MIN_SUFFIX_REUSE, encode_prefix_comb,
    encode_suffix_comb, iter_path_comb
)
from alasio.ext.path.validate import validate_filepath

pathcomb = pytest.importorskip('alasio_speedup.pathcomb')

# the error of the first build/load attempt, None while every test passes
_encoder_error = None


@pytest.fixture(autouse=True)
def c_encoder_available():
    """Skip the tests when the C encoder is not built and cannot be built."""
    global _encoder_error
    if _encoder_error is None:
        try:
            pathcomb.check()
        except Exception as e:
            _encoder_error = str(e)
    if _encoder_error:
        pytest.skip(f'C pathcomb encoder is not available: {_encoder_error}')


def encode_path_comb_python(paths):
    """
    The reference pipeline, the three sections the C encoder emits.

    Args:
        paths (Iterable[str]): Full paths in encoded order

    Returns:
        tuple[list[int], list[int], bytes]: Combined prefix values,
            combined suffix values and the remaining paths concatenated
    """
    list_path = []
    list_prefix_reuse = []
    list_suffix_reuse = []
    list_suffix_lookback = []
    for prefix_reuse, path, suffix_reuse, suffix_lookback in iter_path_comb(paths):
        list_prefix_reuse.append(prefix_reuse)
        list_suffix_reuse.append(suffix_reuse)
        list_suffix_lookback.append(suffix_lookback)
        list_path.append(path.encode())

    return (
        encode_prefix_comb(list_prefix_reuse, [len(path) for path in list_path]),
        encode_suffix_comb(list_suffix_reuse, list_suffix_lookback),
        b''.join(list_path),
    )


def iter_cases():
    """
    The branches of the search, case by case.

    Yields:
        tuple[str, list[str]]: (label, paths)
    """
    yield 'empty', []
    yield 'single', ['a.py']
    # prefix reuse
    yield 'same directory', ['assets/a/1.png', 'assets/a/2.png', 'assets/a/3.png']
    yield 'nested directories', [
        'assets/cn/character/1.png', 'assets/cn/character/2.png',
        'assets/cn/character/deep/3.png', 'assets/cn/other/4.png',
    ]
    # suffix reuse
    yield 'png pair', ['a/1.png', 'b/2.png']
    yield 'same basename', ['x/foo.png', 'y/foo.png', 'z/foo.png']
    yield 'dotless', ['README', 'a/README', 'b/README']
    yield 'multi dot', [
        'assets/character/Firefly.2.png', 'assets/character/Firefly.png',
        'assets/cn/assignment/dispatch/ASSIGNMENT_START.SEARCH.png',
        'assets/cn/assignment/dispatch/ASSIGNMENT_START.png',
    ]
    yield 'bmp then png', ['a/1.png', 'b/2.png', 'f/foo.bmp', 'f/foo.png']
    yield 'extension only, no stem', ['a/1.png', 'b/2.jpg', 'c/3.png']
    # the object level 2 of the search: same extension, same last character
    yield 'same last character', ['assets/aa.png', 'assets/ba.png', 'assets/ca.png']
    # a duplicate refreshes the index of a path where it stands
    yield 'duplicate', ['backend/config.py', 'backend/config.py']
    yield 'duplicate apart', [
        'xaa.png', 'yaa.png', 'zaa.png', 'xaa.png',
    ]
    # prefix and suffix overlap on the path
    yield 'crossing', [
        'zz/QQQQQSSSSSSSSSSSSSSS', 'PPPPPPPPPPPPPPPQQQQQ.py',
        'PPPPPPPPPPPPPPPQQQQQSSSSSSSSSSSSSSS',
    ]
    # the lookback limit: the 300 same group entries before it are out of reach
    yield 'lookback limit', [f'f{i:03d}_aa.png' for i in range(300)] + ['zz_aa.png']
    # characters, not bytes: a shared byte is not a shared character
    yield 'ascii and unicode', ['a/é.png', 'a/è.png', 'a/e.png', 'a/éé.png']
    yield 'chinese paths', [
        'assets/cn/立绘/角色.png', 'assets/cn/立绘/角儿.png',
        'assets/cn/立绘/角色2.png', 'assets/jp/立ち絵/角色.png',
    ]
    yield 'unicode directories', ['资料/关卡/地图.png', '资料/关卡/地图2.png', '资料/关卡/头像.png']
    # empty and odd segments, the search never validates, the caller does
    yield 'trailing slash', ['a/', 'a/b', 'a/b/c', 'ab/c']
    yield 'empty segment', ['a//b', 'a//b/c']
    yield 'dotfile', ['.gitignore', '.gitattributes', 'a/.gitignore']
    yield 'empty path', ['', 'a', '']
    # the prefix reuse of the format is capped
    yield 'prefix cap', ['x' * (MAX_PREFIX_REUSE + 10) + f'.{suffix}' for suffix in ('py', 'txt')]


def iter_fuzz_cases(rounds=200, seed=0):
    """
    Random cases, the alphabet is small on purpose: it makes the prefix and
    the suffix of the paths collide far more often than real ones do.

    Args:
        rounds (int): Number of cases
        seed (int): Seed of the generator, cases are reproducible

    Yields:
        tuple[str, list[str]]: (label, paths)
    """
    rng = random.Random(seed)
    for index in range(rounds):
        n = rng.randrange(0, 200)
        kind = rng.choice(['tiny', 'paths', 'duplicates', 'unicode'])
        if kind == 'tiny':
            paths = [
                ''.join(rng.choice('ab/._x') for _ in range(rng.randrange(0, 12)))
                for _ in range(n)
            ]
        elif kind == 'paths':
            paths = []
            for _ in range(n):
                depth = rng.randrange(1, 4)
                parts = [
                    rng.choice(['assets', 'cn', 'ship', 'a', 'bb', 'x' * rng.randrange(1, 5)])
                    for _ in range(depth)
                ]
                stem = rng.choice(['1', 'ship', 'yy', 'aaa', 'x' * rng.randrange(1, 6)])
                paths.append('/'.join(parts + [stem + rng.choice(['.png', '.py', '', '.txt'])]))
        elif kind == 'duplicates':
            paths = [f'dir{i % 5}/file{i % 7}.png' for i in range(n)]
            rng.shuffle(paths)
        else:
            paths = [
                f'资料{i % 4}/目录{i % 3}/文件{i % 6}.png'
                for i in range(n)
            ]
            rng.shuffle(paths)
        yield f'fuzz#{index} {kind} n={n}', paths


CASES = list(iter_cases()) + list(iter_fuzz_cases())

case_ids = [label for label, _ in CASES]


class TestAgainstReference:
    """The C encoder emits what the Python pipeline emits, byte for byte."""

    @pytest.mark.parametrize('label, paths', CASES, ids=case_ids)
    def test_sections_match(self, label, paths):
        """The three sections of the C encoder equal the reference ones."""
        assert pathcomb.encode_path_comb(paths) == encode_path_comb_python(paths)

    def test_empty(self):
        """No path is no section at all."""
        assert pathcomb.encode_path_comb([]) == ([], [], b'')

    def test_accepts_an_iterator(self):
        """The paths may be a generator, the call site passes one."""
        paths = ['a/1.png', 'a/2.png', 'b/3.png']

        def generate():
            yield from paths

        assert pathcomb.encode_path_comb(generate()) == encode_path_comb_python(paths)


class TestDecode:
    """The decoder of the pack reads the sections back to the input paths."""

    @pytest.mark.parametrize('label, paths', [
        (label, paths) for label, paths in CASES if label in (
            'single', 'same directory', 'nested directories', 'png pair', 'same basename',
            'dotless', 'multi dot', 'bmp then png', 'extension only, no stem',
            'same last character', 'duplicate', 'crossing', 'lookback limit',
            'chinese paths', 'unicode directories', 'dotfile',
        )
    ])
    def test_decode_matches(self, label, paths):
        """decode_prefix_comb / decode_suffix_comb and _decode_paths rebuild the paths."""
        for path in paths:
            validate_filepath(path)
        prefix_comb, suffix_comb, path_data = pathcomb.encode_path_comb(paths)
        prefix_reuse, path_len = decode_prefix_comb(prefix_comb)
        suffix_reuse, suffix_lookback = decode_suffix_comb(suffix_comb)
        assert PackDecodeBase._decode_paths(
            path_data, prefix_reuse, path_len, suffix_reuse, suffix_lookback,
        ) == paths


class TestCombEncoders:
    """The accelerator encodes the two combined encodings on its own."""

    @pytest.mark.parametrize('label, paths', CASES[:24], ids=case_ids[:24])
    def test_prefix_comb_matches(self, label, paths):
        """The C prefix comb equals the Python one on the reuse of the paths."""
        list_prefix_reuse = []
        list_path_length = []
        for prefix_reuse, path, suffix_reuse, suffix_lookback in iter_path_comb(paths):
            list_prefix_reuse.append(prefix_reuse)
            list_path_length.append(len(path.encode()))
        assert pathcomb.encode_prefix_comb(list_prefix_reuse, list_path_length) == \
            encode_prefix_comb(list_prefix_reuse, list_path_length)

    @pytest.mark.parametrize('label, paths', CASES[:24], ids=case_ids[:24])
    def test_suffix_comb_matches(self, label, paths):
        """The C suffix comb equals the Python one on the reuse of the paths."""
        list_suffix_reuse = []
        list_suffix_lookback = []
        for prefix_reuse, path, suffix_reuse, suffix_lookback in iter_path_comb(paths):
            list_suffix_reuse.append(suffix_reuse)
            list_suffix_lookback.append(suffix_lookback)
        assert pathcomb.encode_suffix_comb(list_suffix_reuse, list_suffix_lookback) == \
            encode_suffix_comb(list_suffix_reuse, list_suffix_lookback)

    def test_empty(self):
        """No entry is no section at all."""
        assert pathcomb.encode_prefix_comb([], []) == []
        assert pathcomb.encode_suffix_comb([], []) == []

    def test_length_mismatch(self):
        """The two lists of an encoder must have the same length."""
        with pytest.raises(ValueError):
            pathcomb.encode_prefix_comb([0, 1], [3])
        with pytest.raises(ValueError):
            pathcomb.encode_suffix_comb([0, 1], [0])

    def test_value_out_of_range(self):
        """A value out of the range of the format is refused."""
        with pytest.raises(ValueError):
            pathcomb.encode_prefix_comb([MAX_PREFIX_REUSE + 1], [0])
        with pytest.raises(ValueError):
            pathcomb.encode_prefix_comb([0], [MAX_PATH_LEN + 1])
        with pytest.raises(ValueError):
            pathcomb.encode_suffix_comb([MAX_SUFFIX_REUSE + 1], [0])
        with pytest.raises(ValueError):
            pathcomb.encode_suffix_comb([0], [MAX_SUFFIX_LOOKBACK + 1])


class TestGuards:
    """The inputs the encoder refuses."""

    def test_nul_in_path(self):
        """A NUL would cut the path in two, the encoder refuses it."""
        with pytest.raises(ValueError):
            pathcomb.encode_path_comb(['a/b.py', 'a\0b.py'])

    def test_remaining_path_too_long(self):
        """A remaining path longer than MAX_PATH_LEN cannot be combined."""
        paths = ['x' * (MAX_PATH_LEN + 10)]
        with pytest.raises(ValueError):
            pathcomb.encode_path_comb(paths)
        # the reference refuses it as well, with its own check
        with pytest.raises(ValueError):
            encode_path_comb_python(paths)


class TestParams:
    """The frozen parameters of the pack format, in the library and in Python."""

    def test_library_params(self):
        """The library was built with the parameters this package speaks."""
        values = [0] * len(pathcomb.PARAMS)
        params = (pathcomb.ctypes.c_int64 * len(values))()
        assert pathcomb.library().pathcomb_params(params) == len(values)
        assert tuple(params) == pathcomb.PARAMS

    def test_python_params(self):
        """The accelerator speaks the parameters of the Python encoders."""
        assert PARAMS == (
            MAX_PREFIX_REUSE, MIN_SUFFIX_REUSE, MAX_SUFFIX_REUSE, MAX_SUFFIX_LOOKBACK,
        )
        assert pathcomb.PARAMS == PARAMS

    def test_other_params_refused(self):
        """A library of another pack format is never called."""
        import alasio.ext.algorithm.pathcomb.pathcomb_encode_c as encoder
        from alasio.speedup import accelerator

        if accelerator('pathcomb') is None:
            assert encoder._pathcomb is None
        else:
            assert encoder._pathcomb is not None
            assert encoder._pathcomb.PARAMS == PARAMS
