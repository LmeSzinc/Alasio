"""
A small `pip download --no-deps` for the deploy domain.

The package takes `(name, version)` of a distribution and downloads the wheels
of that version from a PyPI simple index (PEP 503) into the folder the caller
gives. It does not resolve dependencies, does not build source distributions
and does not install anything: the caller decides what happens to the files.

The parts, and the files of pip and packaging they are ported from (the code
is rewritten in the style of this project, the algorithms and the function
names are the ones of the sources). The modules that are a port of a file of
the upstream projects carry a leading underscore; `pypi_file` is the code of
this project:

- `_tags`: the tags of a wheel and the tags of an interpreter, ported from
  `packaging/tags.py`;
- `_manylinux` / `_musllinux`: the linux platform families, ported from
  `packaging/_manylinux.py` and `packaging/_musllinux.py`;
- `_compatibility_tags`: the supported tag set of a target, ported from
  `pip/_internal/utils/compatibility_tags.py`;
- `_target_python`: the interpreter a download selects for, ported from
  `pip/_internal/models/target_python.py`;
- `_version` / `_specifiers`: PEP 440 versions and specifiers, ported from
  `packaging/version.py` and `packaging/specifiers.py`;
- `_utils`: the name and wheel filename helpers, ported from
  `packaging/utils.py`, and `check_requires_python` from
  `pip/_internal/utils/packaging.py`;
- `_wheel`: the wheel model, ported from `pip/_internal/models/wheel.py`;
- `_link`: the links of an index page, ported from
  `pip/_internal/models/link.py` and `pip/_internal/index/collector.py`;
- `pypi_file`: the fetcher itself, `PypiFile.download(name, version, mode)`.

pip is MIT and packaging is Apache-2.0 OR BSD-2-Clause, both allow the port
with the notice kept in the module that carries the code.

Usage:
    from alasio.deploy.pip_download import PypiFile

    pypi = PypiFile(pypi_mirrors, dest)
    files = await pypi.download('msgspec', '0.19.0', 'current')
    await pypi.aclose()
"""

from alasio.deploy.pip_download.pypi_file import (
    DOWNLOAD_MODES, DownloadMode, PypiFile, WheelCandidate, WheelHashError, WheelNotFoundError, select_wheels
)

__all__ = [
    'DOWNLOAD_MODES',
    'DownloadMode',
    'PypiFile',
    'WheelCandidate',
    'WheelHashError',
    'WheelNotFoundError',
    'select_wheels',
]
