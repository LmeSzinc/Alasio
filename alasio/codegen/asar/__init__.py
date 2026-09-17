"""
Electron app.asar packer and unpacker.

Reading an archive::

    from alasio.codegen.asar import AsarArchive

    archive = AsarArchive.read_asar('app.asar')
    print(archive.files['package.json'].size)
    archive.extract_all('output')

Building an archive::

    archive = AsarArchive()
    archive.add_folder('webapp', include=['dist/**', 'package.json'])
    archive.add_file(data=b'{"name":"alasio"}', arc_path='build.json')
    result = archive.write_asar('app.asar')
    print(result.sha256)

Extracting without loading the archive in memory (a single sequential pass)::

    from alasio.codegen.asar import unpack

    unpack('app.asar', 'output', verify=True)
"""
from .archive import (
    REGION_BUDGET as REGION_BUDGET, AsarArchive as AsarArchive, UnpackResult as UnpackResult,
    pack_sha256 as pack_sha256, unpack as unpack
)
from .errors import (
    AsarEntryNotFoundError as AsarEntryNotFoundError, AsarError as AsarError, AsarFormatError as AsarFormatError,
    AsarPathError as AsarPathError, AsarUnsupportedError as AsarUnsupportedError
)
from .model import KIND_DIR as KIND_DIR, KIND_FILE as KIND_FILE, KIND_LINK as KIND_LINK, AsarFileInfo as AsarFileInfo
from .pack import PackResult as PackResult

__all__ = [
    'AsarArchive',
    'AsarEntryNotFoundError',
    'AsarError',
    'AsarFileInfo',
    'AsarFormatError',
    'AsarPathError',
    'AsarUnsupportedError',
    'KIND_DIR',
    'KIND_FILE',
    'KIND_LINK',
    'PackResult',
    'REGION_BUDGET',
    'UnpackResult',
    'pack_sha256',
    'unpack',
]
