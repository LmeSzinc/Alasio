"""
Generate html pages.

A small generator in the shape of alasio.codegen.python: one method per tag that
makes an element and takes it into the current container, a with block that
collects the children, and a document that writes the page skeleton.

Usage:
    from alasio.codegen.html import HtmlGen

    gen = HtmlGen()
    with gen.Doc('Links for httpx2') as page:
        with page.body():
            gen.H1('Links for httpx2')
            with gen.Line():
                gen.A('httpx2-2.13.1-py3-none-any.whl', href='2.13.1/httpx2-2.13.1-py3-none-any.whl')
                gen.Br()
    gen.write('wheel/httpx2/index.html')
"""

from .gen import HtmlGen
from .gen_base import HtmlGenBase
from .obj_base import DEFAULT_WIDTH, HtmlContainer, HtmlError, HtmlObject, NotInline, escape
from .obj_document import Body, Document, Head
from .obj_element import ELEMENTS, TAG_TABLE, VOID_ELEMENTS, Element, Line
from .obj_simple import Comment, Raw, Text

__all__ = [
    'DEFAULT_WIDTH',
    'ELEMENTS',
    'TAG_TABLE',
    'VOID_ELEMENTS',
    'Body',
    'Comment',
    'Document',
    'Element',
    'Head',
    'HtmlContainer',
    'HtmlError',
    'HtmlGen',
    'HtmlGenBase',
    'HtmlObject',
    'Line',
    'NotInline',
    'Raw',
    'Text',
    'escape',
]
