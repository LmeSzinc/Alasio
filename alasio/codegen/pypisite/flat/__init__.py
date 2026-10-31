"""
Generate the human readable index of a folder of wheels: a flat page.

The page is the one a person reads, not the one pip reads. pip reads the anchors
of any page it is given with --find-links, so this page can arrange the wheels
for a reader: one section per project, one section per version of a compiled
project, and a matrix of platforms by python/abi for each version.

The package has four parts:

- tags: the tag fields of a wheel file name, the keys of the matrix and the
  order of them;
- table: the wheels of a version as a matrix, the wheels of a project of pure
  python as a list of versions;
- obj_flat: the sections, the tables and the contents, plus the registry the
  index and the badges read when the page renders;
- gen: PypiSiteFlat, the five methods a page is written with.

Usage:
    from alasio.codegen.pypisite.flat import FLAT_STYLE, PypiSiteFlat, WheelLink

    gen = PypiSiteFlat()
    with gen.Doc('Wheels') as page:
        with page.body():
            gen.inject_index()
            with gen.WheelName('av'):
                with gen.WheelVersion('18.1.0'):
                    gen.WheelTableCompiled([WheelLink('av', '18.1.0', 'av-18.1.0-cp38-cp38-win_amd64.whl')])
    gen.generate_str()
"""

from .gen import PypiSiteFlat, flat_page
from .obj_flat import FlatIndex, FlatProject, FlatRegistry, FlatTableCompiled, FlatTablePure, FlatVersion
from .style import FLAT_STYLE
from .table import FlatCell, VersionTable, build_pure_table, build_version
from .tags import column_key, column_sort_key, is_pure, parse_tags, row_display, row_key, row_sort_key, tag_label

__all__ = [
    'FLAT_STYLE',
    'FlatCell',
    'FlatIndex',
    'FlatProject',
    'FlatRegistry',
    'FlatTableCompiled',
    'FlatTablePure',
    'FlatVersion',
    'PypiSiteFlat',
    'VersionTable',
    'build_pure_table',
    'build_version',
    'column_key',
    'column_sort_key',
    'flat_page',
    'is_pure',
    'parse_tags',
    'row_display',
    'row_key',
    'row_sort_key',
    'tag_label',
]
