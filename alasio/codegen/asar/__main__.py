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
import os
import sys

import msgspec

from alasio.ext.path.atomic import CHUNK_SIZE

from .archive import REGION_BUDGET, AsarArchive, pack_sha256
from .format import read_header
from .model import KIND_DIR, KIND_FILE, KIND_LINK, build_header, canonical_entries


def count_entries(archive):
    """
    Count the entries of an archive by kind.

    Args:
        archive (AsarArchive): Archive to count

    Returns:
        tuple[int, int, int, int]: Packed files, unpacked files, directories and
            links
    """
    packed = 0
    unpacked = 0
    directories = 0
    links = 0
    for _, info in archive.iter_entries():
        if info.kind == KIND_DIR:
            directories += 1
        elif info.kind == KIND_LINK:
            links += 1
        elif info.unpacked:
            unpacked += 1
        else:
            packed += 1
    return packed, unpacked, directories, links


def unpack_archive(archive, dest, verify=False, region_budget=REGION_BUDGET, chunk_size=CHUNK_SIZE):
    """
    Extract a whole archive, for the command line.

    The library returns no statistics, so what the summary needs is counted here
    while the archive is open.

    Args:
        archive (str): Path of the archive
        dest (str): Target directory
        verify (bool): Check every content against the integrity of the header
        region_budget (int): Regions up to this size are read in one piece, 0
            streams everything
        chunk_size (int): Read chunk size of a streamed region

    Returns:
        tuple[int, int, int, int]: Files, directories, unpacked files and links
    """
    with AsarArchive(archive) as asar:
        packed, unpacked, directories, links = count_entries(asar)
        asar.extract_all(dest, verify=verify, region_budget=region_budget, chunk_size=chunk_size)
    return packed + unpacked, directories, unpacked, links


def cmd_pack(args):
    """
    Pack a directory tree into an archive.

    Args:
        args (argparse.Namespace): Command line arguments

    Returns:
        int: Exit code
    """
    with AsarArchive() as archive:
        count = archive.add_folder(
            args.src,
            include=args.file,
            exclude=args.exclude,
            unpack=args.unpack,
            unpack_dir=args.unpack_dir,
        )
        archive.write(args.dest, integrity=not args.no_integrity)
        packed, unpacked, _, _ = count_entries(archive)
        archive_size = os.path.getsize(args.dest)
        print(
            f'Packed {count} entries from {args.src} to {args.dest}\n'
            f'  {packed} files in the archive, {unpacked} unpacked\n'
            f'  archive {archive_size} bytes, header {archive.header_size} bytes, '
            f'data {archive_size - archive.data_offset} bytes\n'
            f'  sha256 {pack_sha256(args.dest)}'
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
    files, directories, unpacked, links = unpack_archive(
        args.archive,
        args.dest,
        region_budget=args.region_budget,
        chunk_size=args.chunk_size,
        verify=args.verify,
    )
    print(
        f'Extracted {files} files and {directories} directories to {args.dest}\n'
        f'  {unpacked} unpacked files, {links} links'
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
    with AsarArchive(args.archive) as archive:
        for path, info in archive.iter_entries():
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
    with AsarArchive(args.archive) as archive:
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
    with AsarArchive(args.archive) as archive:
        info = archive.entry(args.name)
    size = '-' if info.size is None else str(info.size)
    offset = '-' if info.offset is None else str(info.offset)
    print(
        f'{info.kind} {size} {offset} '
        f'{str(info.unpacked).lower()} {str(info.executable).lower()}'
    )
    return 0


def stored_header(archive):
    """
    Read the header JSON of an archive as it is stored in the file.

    The reader keeps the entry table the header describes, not the bytes it was
    written in, and the two are not the same: an archive may store its entries
    in any order while a table that is written back is rebuilt in the canonical
    one, so the file is read again here to print what is really stored.

    Args:
        archive (AsarArchive): Open archive

    Returns:
        bytes: Header JSON, UTF-8 encoded
    """
    fd = archive.fd
    # The frame tells how long the header is, reading it leaves the handle on
    # the first content byte
    fd.seek(0)
    return read_header(fd)[0]


def cmd_header(args):
    """
    Print the header JSON of an archive.

    Args:
        args (argparse.Namespace): Command line arguments

    Returns:
        int: Exit code
    """
    with AsarArchive(args.archive) as archive:
        if args.entries:
            # Rebuilt from the entry table, useful to compare with what a pack writes
            data = msgspec.json.encode(build_header(canonical_entries(archive.files)))
        else:
            data = stored_header(archive)
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
