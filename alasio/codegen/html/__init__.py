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

from .gen import HtmlGen as HtmlGen
from .gen_base import HtmlGenBase as HtmlGenBase
from .obj_base import (
    DEFAULT_WIDTH as DEFAULT_WIDTH, HtmlContainer as HtmlContainer, HtmlError as HtmlError, HtmlObject as HtmlObject,
    NotInline as NotInline, escape as escape
)
from .obj_document import Body as Body, Document as Document, Head as Head
from .obj_element import (
    ELEMENTS as ELEMENTS, TAG_TABLE as TAG_TABLE, VOID_ELEMENTS as VOID_ELEMENTS, Element as Element, Line as Line
)
from .obj_simple import Comment as Comment, Raw as Raw, Text as Text
from .write import is_current as is_current, write_if_changed as write_if_changed
