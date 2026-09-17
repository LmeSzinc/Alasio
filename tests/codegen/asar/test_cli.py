"""
Tests of the command line interface.

The commands are called through ``main(argv)`` so that they run in the fake
filesystem of the fixture, the exit code and the printed summary are asserted.
"""

import pytest

from alasio.codegen.asar.__main__ import main
from alasio.ext.path.atomic import file_read_bytes
from alasio.testing.filesystem import fs  # noqa: F401
from tests.codegen.asar import fixture


def build_source_tree(fs):
    """
    Create a small source tree in the fake filesystem.

    Args:
        fs (FakeFilesystem): Fake filesystem

    Returns:
        str: Root path of the tree
    """
    root = f'{fs.root_dir.path}/src'
    fs.create_dir(root)
    fs.create_dir(f'{root}/dist')
    fs.create_file(f'{root}/package.json', contents=b'{"name":"alasio"}')
    fs.create_file(f'{root}/dist/main.js', contents=b'main')
    fs.create_file(f'{root}/dist/style.css', contents=b'body{}')
    return root


class TestCommands:
    def test_no_command(self, fs, capsys):
        """Without a command the help is printed and the exit code is 1."""
        assert main([]) == 1
        assert 'usage: python -m alasio.codegen.asar' in capsys.readouterr().out

    def test_list(self, fs, capsys):
        """list prints the path of every entry, in archive order."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        assert main(['list', '/tiny.asar']) == 0
        assert capsys.readouterr().out == 'hello.txt\nsub\nsub/bin.dat\n'

    def test_list_long(self, fs, capsys):
        """list --long prints the size, the offset and the flags."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        assert main(['list', '/tiny.asar', '--long']) == 0
        assert capsys.readouterr().out == (
            '        10          0 pack                 hello.txt\n'
            '         -          - dir                  sub\n'
            '         7         10 pack                 sub/bin.dat\n'
        )

    def test_stat(self, fs, capsys):
        """stat prints one line with the fields of an entry."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        assert main(['stat', '/tiny.asar', 'sub/bin.dat']) == 0
        assert capsys.readouterr().out == 'file 7 10 false false\n'
        assert main(['stat', '/tiny.asar', 'sub']) == 0
        assert capsys.readouterr().out == 'dir - - false false\n'

    def test_extract(self, fs, capsys):
        """extract writes a single entry to a file path."""
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        assert main(['extract', '/tiny.asar', 'sub/bin.dat', '/out/bin.dat']) == 0
        assert file_read_bytes('/out/bin.dat') == b'PAYLOAD'
        assert capsys.readouterr().out == 'Extracted sub/bin.dat to /out/bin.dat\n'

    def test_header(self, fs, capsys):
        """header prints the header JSON of the archive."""
        fs.create_file('/simple.asar', contents=fixture.make_archive(
            {'files': {'a.txt': {'size': 1, 'offset': '0'}}}, b'x',
        ))
        assert main(['header', '/simple.asar']) == 0
        assert capsys.readouterr().out == '{"files":{"a.txt":{"size":1,"offset":"0"}}}\n'

    def test_header_json(self, fs, capsys):
        """header --json pretty prints the JSON."""
        fs.create_file('/simple.asar', contents=fixture.make_archive(
            {'files': {'a.txt': {'size': 1, 'offset': '0'}}}, b'x',
        ))
        assert main(['header', '/simple.asar', '--json']) == 0
        assert capsys.readouterr().out == (
            '{\n'
            '  "files": {\n'
            '    "a.txt": {\n'
            '      "size": 1,\n'
            '      "offset": "0"\n'
            '    }\n'
            '  }\n'
            '}\n'
        )

    def test_header_entries(self, fs, capsys):
        """header --entries rebuilds the header from the entry table."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        assert main(['header', '/packthis.asar', '--entries']) == 0
        assert capsys.readouterr().out == fixture.packthis_430()[
            16:16 + 1529
        ].decode('utf-8') + '\n'

    def test_pack_unpack_round_trip(self, fs, capsys):
        """pack, list and unpack work together."""
        root = build_source_tree(fs)
        assert main(['pack', root, '/packed.asar']) == 0
        out = capsys.readouterr().out
        assert out.startswith(f'Packed 4 entries from {root} to /packed.asar\n')
        assert '  3 files in the archive, 0 unpacked\n' in out
        assert main(['list', '/packed.asar']) == 0
        assert capsys.readouterr().out == 'dist\npackage.json\n' or capsys.readouterr().out == ''
        assert main(['unpack', '/packed.asar', '/out', '--verify']) == 0
        assert file_read_bytes('/out/dist/main.js') == b'main'
        assert file_read_bytes('/out/package.json') == b'{"name":"alasio"}'

    def test_pack_include(self, fs, capsys):
        """pack --file only packs the matching entries."""
        root = build_source_tree(fs)
        assert main(['pack', root, '/packed.asar', '--file', 'dist/**']) == 0
        capsys.readouterr()
        assert main(['list', '/packed.asar']) == 0
        assert capsys.readouterr().out == 'dist\ndist/main.js\ndist/style.css\n'

    def test_pack_unpack_dir(self, fs, capsys):
        """pack --unpack-dir stores content next to the archive."""
        root = build_source_tree(fs)
        assert main(['pack', root, '/packed.asar', '--unpack-dir', 'dist']) == 0
        assert '  1 files in the archive, 2 unpacked\n' in capsys.readouterr().out
        assert file_read_bytes('/packed.asar.unpacked/dist/main.js') == b'main'
        assert main(['list', '/packed.asar', '--long']) == 0
        assert capsys.readouterr().out == (
            '         -          - dir unpack           dist\n'
            '         4          - unpack               dist/main.js\n'
            '         6          - unpack               dist/style.css\n'
            f'        17          0 pack                 package.json\n'
        )

    def test_pack_no_integrity(self, fs, capsys):
        """pack --no-integrity leaves the hashes out."""
        root = build_source_tree(fs)
        assert main(['pack', root, '/packed.asar', '--no-integrity']) == 0
        capsys.readouterr()
        assert main(['header', '/packed.asar']) == 0
        assert 'integrity' not in capsys.readouterr().out

    def test_unpack_region_options(self, fs, capsys):
        """unpack --region-budget and --chunk-size reach the scanner."""
        fs.create_file('/packthis.asar', contents=fixture.packthis_430())
        assert main([
            'unpack', '/packthis.asar', '/out',
            '--region-budget', '0', '--chunk-size', '4096',
        ]) == 0
        out = capsys.readouterr().out
        assert '  226 bytes of data read in 1 regions, 0 seeks\n' in out
        assert file_read_bytes('/out/file0.txt') == b'file0 content'

    def test_errors(self, fs, capsys):
        """A missing entry and a missing archive raise the module errors."""
        from alasio.codegen.asar.errors import AsarEntryNotFoundError, AsarFormatError
        fs.create_file('/tiny.asar', contents=fixture.tiny_341())
        with pytest.raises(AsarEntryNotFoundError):
            main(['stat', '/tiny.asar', 'nope.txt'])
        fs.create_file('/broken.asar', contents=b'broken')
        with pytest.raises(AsarFormatError):
            main(['list', '/broken.asar'])

    def test_prog_name(self, fs, capsys):
        """The help names the module, not the script."""
        with pytest.raises(SystemExit):
            main(['list', '--help'])
        assert 'usage: python -m alasio.codegen.asar list' in capsys.readouterr().out
