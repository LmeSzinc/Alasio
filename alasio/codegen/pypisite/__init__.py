"""
Generate the pages of a pip index.

The module is the domain layer on top of alasio.codegen.html: the generic
generator writes any html page, and PypiSite writes the two pages pip reads from
a folder of wheels:

    {folder}/index.html             every wheel of the folder, for --find-links
    {folder}/{name}/index.html      the wheels of one distribution, PEP 503

A wheel is described by a WheelLink, which builds the link of the page from the
name, the version and the file name, so a page never points outside the folder
the static host serves, and every link carries the sha256 pip verifies.

The pages of a run are rendered from the files the run published, never from a
listing of the folder: a page never lists a file that is not there.

Usage:
    from alasio.codegen.pypisite import PypiSite, WheelLink

    link = WheelLink('httpx2', '2.13.1', 'httpx2-2.13.1-py3-none-any.whl', sha256)
    gen = PypiSite()
    with gen.PypiHeader('httpx2'):
        gen.Wheels([link])
    gen.write('wheel/httpx2/index.html')
"""

from .gen import PypiSite, find_links_page, simple_page
from .obj_pypi import PypiHeader, Wheel
from .wheel import WheelLink, is_name, normalize_name, prepare_links, version_key

__all__ = [
    'PypiHeader',
    'PypiSite',
    'Wheel',
    'WheelLink',
    'find_links_page',
    'is_name',
    'normalize_name',
    'prepare_links',
    'simple_page',
    'version_key',
]
