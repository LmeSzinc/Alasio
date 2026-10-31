"""
Tests for the document of a page: the doctype, the head and the body.
"""
import pytest

from alasio.codegen.html import Body, Document, Element, Head, HtmlError, HtmlGen, NotInline


class TestDocumentRender:
    def test_empty(self):
        gen = HtmlGen()
        gen.Doc('Title')
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Title</title>
  </head>
  <body>
  </body>
</html>
"""

    def test_head_and_body(self):
        gen = HtmlGen()
        with gen.Doc('Links for httpx2') as page:
            with page.head():
                gen.Meta(name='pypi:repository-version', content='1.0')
            with page.body():
                gen.H1('Links for httpx2')
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for httpx2</title>
  </head>
  <body>
    <h1>Links for httpx2</h1>
  </body>
</html>
"""

    def test_lang(self):
        gen = HtmlGen()
        gen.Doc('T', lang='zh-CN')
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <title>T</title>
  </head>
  <body>
  </body>
</html>
"""

    def test_charset(self):
        gen = HtmlGen()
        gen.Doc('T', charset='utf-16')
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-16">
    <title>T</title>
  </head>
  <body>
  </body>
</html>
"""

    def test_escapes(self):
        gen = HtmlGen()
        gen.Doc('a & <b>', lang='en"')
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en&quot;">
  <head>
    <meta charset="utf-8">
    <title>a &amp; &lt;b&gt;</title>
  </head>
  <body>
  </body>
</html>
"""

    def test_head_before_body_whatever_the_order(self):
        """The head is rendered before the body, whatever the caller wrote first."""
        gen = HtmlGen()
        with gen.Doc('T') as page:
            with page.body():
                gen.P('x')
            with page.head():
                gen.Meta(name='a', content='b')
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="a" content="b">
    <title>T</title>
  </head>
  <body>
    <p>x</p>
  </body>
</html>
"""


class TestDocumentItems:
    def test_constructor_items(self):
        """A detached item given to the constructor lands at the level of the body."""
        gen = HtmlGen()
        title = Element(gen, 'h1', 'Title')
        note = Element(gen, 'p', 'note')
        gen.Doc('T', body=[title, note])
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>T</title>
  </head>
  <body>
    <h1>Title</h1>
    <p>note</p>
  </body>
</html>
"""

    def test_attached_item_refused(self):
        """gen.H1() already took the object into the page."""
        gen = HtmlGen()
        with pytest.raises(HtmlError, match='is already in the page'):
            gen.Doc('T', body=[gen.H1('Title')])

    def test_constructor_head_items(self):
        gen = HtmlGen()
        meta = Element(gen, 'meta', name='a', content='b')
        gen.Doc('T', head=[meta])
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="a" content="b">
    <title>T</title>
  </head>
  <body>
  </body>
</html>
"""

    def test_direct_child_refused(self):
        """A meta that silently lands in the body is a bug worth an error."""
        gen = HtmlGen()
        with pytest.raises(HtmlError, match='takes no item of its own'):
            with gen.Doc('T'):
                gen.Meta(name='a', content='b')

    def test_direct_text_refused(self):
        gen = HtmlGen()
        with pytest.raises(HtmlError, match='takes no item of its own'):
            with gen.Doc('T'):
                gen.Text('x')

    def test_head_and_body_are_shared(self):
        """head() answers the same box every time, so two blocks add up."""
        gen = HtmlGen()
        with gen.Doc('T') as page:
            with page.head():
                gen.Meta(name='a', content='b')
            with page.head():
                gen.Meta(name='c', content='d')
            with page.body():
                gen.P('x')
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="a" content="b">
    <meta name="c" content="d">
    <title>T</title>
  </head>
  <body>
    <p>x</p>
  </body>
</html>
"""

    def test_nested_content(self):
        gen = HtmlGen()
        with gen.Doc('T') as page:
            with page.body():
                with gen.Ul(class_='list'):
                    for name in ('a', 'b'):
                        with gen.Li().wrap('inline'):
                            gen.A(name, href=f'{name}/')
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>T</title>
  </head>
  <body>
    <ul class="list">
      <li><a href="a/">a</a></li>
      <li><a href="b/">b</a></li>
    </ul>
  </body>
</html>
"""


class TestDocumentObject:
    def test_types(self):
        gen = HtmlGen()
        page = gen.Doc('T')
        assert isinstance(page, Document)
        assert isinstance(page.head(), Head)
        assert isinstance(page.body(), Body)

    def test_head_title(self):
        gen = HtmlGen()
        page = gen.Doc('T')
        assert page.head().title == 'T'
        assert page.title == 'T'

    def test_not_inline(self):
        """A whole page is never one line of another page."""
        gen = HtmlGen()
        page = gen.Doc('T')
        with pytest.raises(NotInline):
            page.inline_str()

    def test_repr(self):
        gen = HtmlGen()
        assert repr(gen.Doc('T')) == "<Document 'T'>"

    def test_repr_of_boxes(self):
        gen = HtmlGen()
        page = gen.Doc('T')
        assert repr(page.head()) == "<Head 'T'>"
        assert repr(page.body()) == '<Body 0 child(ren)>'

    def test_not_inline_message(self):
        gen = HtmlGen()
        with pytest.raises(NotInline, match='A document is never rendered on one line'):
            gen.Doc('T').inline_str()

    def test_head_not_inline(self):
        """A box of the document is not a fragment of another page."""
        gen = HtmlGen()
        page = gen.Doc('T')
        with pytest.raises(NotInline, match='<Head> is not rendered on one line'):
            page.head().inline_str()


class TestDocumentEntry:
    def test_entry_restores_parent(self):
        gen = HtmlGen()
        with gen.Doc('T') as page:
            assert gen.context is page
            with page.body():
                assert gen.context is page.body()
            assert gen.context is page
        assert gen.context is None
        assert gen.indent == 0
