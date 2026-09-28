"""
Tests for the import behaviour of the accelerators.

The accelerators are for the server side: a client that only reads packs
must not import an encoder, and must not look for the installed
alasio_speedup either. Importing ``alasio.ext.algorithm.bit2coding`` (or
its bit2coding_decode module) has to leave the encoder alone, the
accelerator is only resolved when an encoder is actually used.

The imports of a process cannot be undone, so every check runs in a fresh
interpreter, see tests/backend/import_testing.py.

Note for the callbacks below: they are the functions of the subprocess, at
module level so that spawn can pickle them, and they must not import
alasio at the module level of this file, the modules imported before the
baseline are not reported.
"""
from tests.backend.import_testing import HeavyImportTest, run_import_test_isolated

# what a decode only client must not import: the encoder modules, and the
# accelerator loader, which scans the site-packages for alasio_speedup and
# builds the library of the accelerator on first use
ENCODER_MODULES = {
    'alasio.speedup',
    'alasio_speedup',
    'alasio.ext.algorithm.bit2coding.bit2coding_encode_python',
    'alasio.ext.algorithm.bit2coding.bit2coding_encode_c',
    'alasio.ext.algorithm.bit2coding.vlenint_encode_python',
    'alasio.ext.algorithm.bit2coding.vlenint_encode_c',
    'alasio.ext.algorithm.pathcomb.pathcomb_encode_c',
    'alasio.ext.algorithm.pathcomb.pathcomb_encode_python',
}


def import_bit2coding_package():
    """Import the package itself, it is a namespace and holds no encoder."""
    import alasio.ext.algorithm.bit2coding  # noqa: F401


def import_decode_from_module():
    """Import the decoder from its own module, the client path."""
    from alasio.ext.algorithm.bit2coding.bit2coding_decode import decode_bit2  # noqa: F401


def import_decode_and_use():
    """Import the decoder and decode a stream, with no encoder around."""
    from alasio.ext.algorithm.bit2coding.bit2coding_decode import decode_bit2

    # b'\x02\x16' is encode_bit2([1, 2]) written by hand, the encoder of
    # this process stays out of the picture
    values, read = decode_bit2(b'\x02\x16')
    assert values == [1, 2], values
    assert read == 2, read


def import_encode_and_use():
    """Import the encoder and encode, the server path."""
    from alasio.ext.algorithm.bit2coding.bit2coding_encode_c import encode_bit2

    encoded = encode_bit2([0, 1, 2, 3, 3, 3, 0, 1])
    assert isinstance(encoded, bytes), encoded
    assert encoded, 'the encoder returned an empty payload'


def import_pack_decoder():
    """Import the pack decoder, the entry point of a client."""
    from alasio.deploy.pack import decode_base, decode_manifest  # noqa: F401


class TestDecodeOnly:
    """A client that only decodes does not touch the encoder."""

    def test_package_import(self):
        """Importing the package itself leaves the encoder alone."""
        HeavyImportTest(ENCODER_MODULES, 'bit2coding package import').run_test(import_bit2coding_package)

    def test_decode_module_import(self):
        """decode_bit2 of bit2coding_decode leaves the encoder alone."""
        HeavyImportTest(ENCODER_MODULES, 'bit2coding_decode import').run_test(import_decode_from_module)

    def test_decode_works_without_the_encoder(self):
        """Decoding works in an interpreter that never imported an encoder."""
        HeavyImportTest(ENCODER_MODULES, 'bit2coding decode use').run_test(import_decode_and_use)

    def test_pack_decoder_import(self):
        """The pack decoder of a client leaves the encoder alone."""
        HeavyImportTest(ENCODER_MODULES, 'pack decoder import').run_test(import_pack_decoder)


class TestEncoderUse:
    """The encoder is imported when it is used, and only then."""

    def test_using_the_encoder_resolves_it(self):
        """encode_bit2() imports the encoder and the loader of the accelerator."""
        violations = run_import_test_isolated(
            import_encode_and_use, {'alasio.speedup'}, 'bit2coding encode use',
        )
        assert violations, 'using encode_bit2() did not import alasio.speedup'
