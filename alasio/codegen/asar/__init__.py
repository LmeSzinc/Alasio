"""
Electron app.asar packer and unpacker.

Reading an archive, and extracting it with a single sequential pass over the
data area::

    from alasio.codegen.asar import AsarArchive

    with AsarArchive('app.asar') as archive:
        print(archive.entry('package.json').size)
        archive.extract_all('output')

Building an archive from a directory::

    with AsarArchive() as archive:
        archive.add_folder('build/app')
        archive.add_file(data=b'{"name":"alasio"}', arc_path='build.json')
        archive.write('app.asar')

Updating the archive of a client in place, one entry at a time::

    with AsarArchive('app.asar') as archive:
        archive.add_file('build/main.js', 'dist/main.js')
        archive.del_folder('dist/renderer')
        archive.write()
"""
from .archive import DEFAULT_MAX_SIZE as DEFAULT_MAX_SIZE, AsarArchive as AsarArchive, pack_sha256 as pack_sha256
from .errors import (
    AsarEntryNotFoundError as AsarEntryNotFoundError, AsarError as AsarError, AsarFormatError as AsarFormatError,
    AsarPathError as AsarPathError, AsarUnsupportedError as AsarUnsupportedError
)
from .format import (
    BLOCK_SIZE as BLOCK_SIZE, MAX_ENTRY_COUNT as MAX_ENTRY_COUNT, MAX_HEADER_SIZE as MAX_HEADER_SIZE,
    MAX_PATH_DEPTH as MAX_PATH_DEPTH
)
from .model import KIND_DIR as KIND_DIR, KIND_FILE as KIND_FILE, KIND_LINK as KIND_LINK, AsarFileInfo as AsarFileInfo
from .source import (
    ContentSource as ContentSource, LocalFileSource as LocalFileSource, MemorySource as MemorySource,
    RangeSource as RangeSource
)

__all__ = [
    'AsarArchive',
    'AsarEntryNotFoundError',
    'AsarError',
    'AsarFileInfo',
    'AsarFormatError',
    'AsarPathError',
    'AsarUnsupportedError',
    'BLOCK_SIZE',
    'ContentSource',
    'DEFAULT_MAX_SIZE',
    'KIND_DIR',
    'KIND_FILE',
    'KIND_LINK',
    'LocalFileSource',
    'MAX_ENTRY_COUNT',
    'MAX_HEADER_SIZE',
    'MAX_PATH_DEPTH',
    'MemorySource',
    'RangeSource',
    'pack_sha256',
]
