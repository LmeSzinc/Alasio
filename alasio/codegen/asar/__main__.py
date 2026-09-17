"""
Command line interface of the asar packer / unpacker::

    python -m alasio.codegen.asar pack <src> <dest> [--file PATTERN] [--exclude PATTERN]
    python -m alasio.codegen.asar unpack <archive> <dest> [--verify]
    python -m alasio.codegen.asar list <archive> [--long]
    python -m alasio.codegen.asar extract <archive> <name> <dest>
    python -m alasio.codegen.asar stat <archive> <name>
    python -m alasio.codegen.asar header <archive> [--json]

The same features are available from Python, see ``AsarArchive``.
"""
import argparse
import sys

import msgspec

from alasio.ext.path.atomic import CHUNK_SIZE

from .archive import REGION_BUDGET, AsarArchive, check_header_size, unpack
from .format import parse_header_pickle, parse_size_pickle
from .model import KIND_FILE, build_header, encode_header


def read_header_json(archive):
    """
    Read the header JSON of an archive the way it is stored.

    Args:
        archive (str): Path of the archive

    Returns:
        bytes: Header JSON, UTF-8 encoded
    """
    with open(archive, 'rb') as f:
        data = f.read()
    header_size = parse_size_pickle(data[:8])
    check_header_size(header_size, len(data))
    return bytes(parse_header_pickle(data[8:8 + header_size]))


def cmd_pack(args):
    """
    Pack a directory tree into an archive.

    Args:
        args (argparse.Namespace): Command line arguments

    Returns:
        int: Exit code
    """
    archive = AsarArchive()
    count = archive.add_folder(
        args.src,
        include=args.file,
        exclude=args.exclude,
        unpack=args.unpack,
        unpack_dir=args.unpack_dir,
    )
    result = archive.write_asar(args.dest, integrity=not args.no_integrity)
    print(
        f'Packed {count} entries from {args.src} to {result.dest}\n'
        f'  {result.file_count} files in the archive, {result.unpacked_count} unpacked\n'
        f'  archive {result.archive_size} bytes, header {result.header_size} bytes, '
        f'data {result.data_size} bytes\n'
        f'  sha256 {result.sha256}'
    )
    return 0


def cmd_unpack(args):
    """
    Extract a whole archive to a directory.

    Args:
        args (argparse.Namespace): Command line arguments

    Returns:
        int: Exit code
    """
    result = unpack(
        args.archive,
        args.dest,
        region_budget=args.region_budget,
        chunk_size=args.chunk_size,
        verify=args.verify,
    )
    print(
        f'Extracted {result.file_count} files and {result.dir_count} directories '
        f'to {result.dest}\n'
        f'  {result.unpacked_count} unpacked files, {result.link_count} links\n'
        f'  {result.data_size} bytes of data read in {result.region_count} regions, '
        f'{result.seek_count} seeks'
    )
    return 0


def cmd_list(args):
    """
    List the entries of an archive.

    Args:
        args (argparse.Namespace): Command line arguments

    Returns:
        int: Exit code
    """
    archive = AsarArchive.read_asar(args.archive)
    for path, info in archive.files.items():
        if not args.long:
            print(path)
            continue
        size = '-' if info.size is None else str(info.size)
        offset = '-' if info.offset is None else str(info.offset)
        if info.kind != KIND_FILE:
            flags = info.kind
            if info.unpacked:
                flags += ' unpack'
        else:
            flags = 'unpack' if info.unpacked else 'pack'
            if info.executable:
                flags += ' executable'
        print(f'{size:>10} {offset:>10} {flags:<20} {path}')
    return 0


def cmd_extract(args):
    """
    Extract a single entry.

    Args:
        args (argparse.Namespace): Command line arguments

    Returns:
        int: Exit code
    """
    archive = AsarArchive.read_asar(args.archive)
    archive.extract_file(args.name, args.dest)
    print(f'Extracted {args.name} to {args.dest}')
    return 0


def cmd_stat(args):
    """
    Print one line with the fields of an entry.

    Args:
        args (argparse.Namespace): Command line arguments

    Returns:
        int: Exit code
    """
    archive = AsarArchive.read_asar(args.archive)
    info = archive.entry(args.name)
    size = '-' if info.size is None else str(info.size)
    offset = '-' if info.offset is None else str(info.offset)
    print(
        f'{info.kind} {size} {offset} '
        f'{str(info.unpacked).lower()} {str(info.executable).lower()}'
    )
    return 0


def cmd_header(args):
    """
    Print the header JSON of an archive.

    Args:
        args (argparse.Namespace): Command line arguments

    Returns:
        int: Exit code
    """
    if args.entries:
        # Rebuilt from the entry table, useful to compare with what a pack writes
        data = encode_header(build_header(AsarArchive.read_asar(args.archive).files))
    else:
        data = read_header_json(args.archive)
    if args.json:
        data = msgspec.json.format(data, indent=2)
    sys.stdout.write(data.decode('utf-8'))
    sys.stdout.write('\n')
    return 0


def main(argv=None):
    """
    Run the command line interface.

    Args:
        argv (list): Arguments, defaults to sys.argv[1:]

    Returns:
        int: Exit code
    """
    parser = argparse.ArgumentParser(
        prog='python -m alasio.codegen.asar',
        description='Pack and unpack Electron app.asar archives.',
    )
    subparsers = parser.add_subparsers(dest='command', metavar='command')

    pack = subparsers.add_parser('pack', help='pack a directory into an archive')
    pack.add_argument('src', help='source directory')
    pack.add_argument('dest', help='target archive path')
    pack.add_argument('--file', action='append', metavar='PATTERN',
                      help='only pack files matching the pattern, can be repeated')
    pack.add_argument('--exclude', action='append', metavar='PATTERN',
                      help='drop files matching the pattern, can be repeated')
    pack.add_argument('--unpack', action='append', metavar='PATTERN',
                      help='store matching files next to the archive, can be repeated')
    pack.add_argument('--unpack-dir', action='append', metavar='PATTERN',
                      help='store matching directories next to the archive, can be repeated')
    pack.add_argument('--no-integrity', action='store_true',
                      help='do not write the per file SHA256 integrity')
    pack.set_defaults(func=cmd_pack)

    unpack_cmd = subparsers.add_parser('unpack', help='extract a whole archive')
    unpack_cmd.add_argument('archive', help='archive path')
    unpack_cmd.add_argument('dest', help='target directory')
    unpack_cmd.add_argument('--region-budget', type=int, default=REGION_BUDGET,
                            help=f'regions up to this size are read in one piece, '
                                 f'default {REGION_BUDGET}')
    unpack_cmd.add_argument('--chunk-size', type=int, default=CHUNK_SIZE,
                            help=f'read chunk size of a streamed region, default {CHUNK_SIZE}')
    unpack_cmd.add_argument('--verify', action='store_true',
                            help='check every file against the integrity of the header')
    unpack_cmd.set_defaults(func=cmd_unpack)

    list_cmd = subparsers.add_parser('list', help='list the entries of an archive')
    list_cmd.add_argument('archive', help='archive path')
    list_cmd.add_argument('--long', action='store_true',
                          help='also print size, offset and flags')
    list_cmd.set_defaults(func=cmd_list)

    extract = subparsers.add_parser('extract', help='extract a single entry')
    extract.add_argument('archive', help='archive path')
    extract.add_argument('name', help='path of the entry inside the archive')
    extract.add_argument('dest', help='target file path')
    extract.set_defaults(func=cmd_extract)

    stat = subparsers.add_parser('stat', help='print the fields of one entry')
    stat.add_argument('archive', help='archive path')
    stat.add_argument('name', help='path of the entry inside the archive')
    stat.set_defaults(func=cmd_stat)

    header = subparsers.add_parser('header', help='print the header JSON')
    header.add_argument('archive', help='archive path')
    header.add_argument('--json', action='store_true', help='pretty print the JSON')
    header.add_argument('--entries', action='store_true',
                        help='print the header rebuilt from the entry table')
    header.set_defaults(func=cmd_header)

    args = parser.parse_args(argv)
    if not getattr(args, 'func', None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
