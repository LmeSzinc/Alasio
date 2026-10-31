"""
The wheels the unit tests of a flat page write pages from.

The names are the ones a page of a folder of wheels really shows: a wheel of
pure python, a wheel per platform, and the digest of the file. The digests are
filled with one letter, so a test reads its own link back.
"""

__all__ = [
    'DIGEST_A',
    'DIGEST_B',
    'FILE_MAC',
    'FILE_PURE',
    'FILE_WIN',
]

# the digest of a wheel, the shape the fetcher of the pack server records
DIGEST_A = 'a' * 64
DIGEST_B = 'b' * 64

# the wheels of the tests: one of pure python and one per platform
FILE_PURE = 'httpx2-2.13.1-py3-none-any.whl'
FILE_WIN = 'httpx2-2.13.1-cp38-cp38-win_amd64.whl'
FILE_MAC = 'httpx2-2.13.1-cp39-cp39-macosx_11_0_arm64.whl'
