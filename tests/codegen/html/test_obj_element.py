"""
Tests for the elements of a page: tags, attributes, children and layout.
"""
import pytest

from alasio.codegen.html import ELEMENTS, TAG_TABLE, VOID_ELEMENTS, Element, HtmlError, HtmlGen, Text


class TestTagTable:
    def test_every_tag_has_a_void_flag(self):
        assert set(TAG_TABLE) == set(ELEMENTS) | set(VOID_ELEMENTS)

    def test_void_flags(self):
        assert all(TAG_TABLE[tag] is True for tag in VOID_ELEMENTS)
        assert all(TAG_TABLE[tag] is False for tag in ELEMENTS)

    def test_no_duplicate(self):
        assert not set(ELEMENTS) & set(VOID_ELEMENTS)

    @pytest.mark.parametrize('tag', ['br', 'meta', 'link', 'img', 'input', 'hr'])
    def test_common_void(self, tag):
        assert TAG_TABLE[tag] is True

    @pytest.mark.parametrize('tag', ['div', 'p', 'a', 'h1', 'script', 'style'])
    def test_common_content(self, tag):
        assert TAG_TABLE[tag] is False


class TestElementRender:
    def test_text(self):
        gen = HtmlGen()
        gen.P('hello')
        assert gen.generate_str() == """\
<p>hello</p>
"""

    def test_text_escaped(self):
        gen = HtmlGen()
        gen.P('<b>hi</b>')
        assert gen.generate_str() == """\
<p>&lt;b&gt;hi&lt;/b&gt;</p>
"""

    def test_empty(self):
        gen = HtmlGen()
        gen.Div()
        assert gen.generate_str() == """\
<div></div>
"""

    def test_attribute(self):
        gen = HtmlGen()
        gen.A('x', href='a/b.html')
        assert gen.generate_str() == """\
<a href="a/b.html">x</a>
"""

    def test_attributes(self):
        gen = HtmlGen()
        gen.Meta(charset='utf-8')
        gen.A('x', href='y', class_=['card', 'wide'], hidden=True, title=None)
        assert gen.generate_str() == """\
<meta charset="utf-8">
<a href="y" class="card wide" hidden>x</a>
"""

    def test_tag_lowercase(self):
        gen = HtmlGen()
        gen.El('DIV', 'x')
        assert gen.generate_str() == """\
<div>x</div>
"""

    def test_custom_element(self):
        gen = HtmlGen()
        gen.El('my-widget', 'x')
        assert gen.generate_str() == """\
<my-widget>x</my-widget>
"""

    def test_number_children(self):
        gen = HtmlGen()
        gen.Span(3, 1.5)
        assert gen.generate_str() == """\
<span>31.5</span>
"""

    def test_none_skipped(self):
        """A conditional child needs no branch around the element."""
        gen = HtmlGen()
        gen.P('a', None, 'b')
        assert gen.generate_str() == """\
<p>ab</p>
"""

    def test_list_flattened(self):
        gen = HtmlGen()
        gen.Ul([Element(gen, 'li', 'a'), [Element(gen, 'li', 'b')]])
        assert gen.generate_str() == """\
<ul><li>a</li><li>b</li></ul>
"""

    def test_bytes_refused(self):
        gen = HtmlGen()
        with pytest.raises(HtmlError, match='is bytes'):
            gen.P(b'x')

    def test_set_refused(self):
        gen = HtmlGen()
        with pytest.raises(TypeError, match='is not an html object, a text or a number'):
            gen.P({'x'})

    def test_attributes_kept(self):
        gen = HtmlGen()
        item = gen.A('x', href='y')
        assert item.attrs == {'href': 'y'}
        assert item.tag == 'a'
        assert item.is_void is False


class TestVoidElement:
    @pytest.mark.parametrize('tag', ['br', 'hr', 'img', 'meta', 'link', 'input', 'wbr'])
    def test_render(self, tag):
        """The element of a void tag renders no closing tag."""
        gen = HtmlGen()
        gen.El(tag)
        assert gen.generate_str() == f"""\
<{tag}>
"""

    def test_attributes(self):
        gen = HtmlGen()
        gen.Img(src='a.png', alt='a')
        assert gen.generate_str() == """\
<img src="a.png" alt="a">
"""

    def test_with_content(self):
        gen = HtmlGen()
        with pytest.raises(ValueError, match='has no content'):
            gen.Br('x')

    def test_with_none_content(self):
        """A None child is not content, it is a conditional that resolved to nothing."""
        gen = HtmlGen()
        gen.Br(None)
        assert gen.generate_str() == """\
<br>
"""

    def test_enter(self):
        gen = HtmlGen()
        with pytest.raises(HtmlError, match='has no content'):
            with gen.Br():
                pass


class TestLayout:
    def test_leaf_inline(self):
        gen = HtmlGen()
        gen.Div(Element(gen, 'span', 'a'), Element(gen, 'span', 'b'))
        assert gen.generate_str() == """\
<div><span>a</span><span>b</span></div>
"""

    def test_with_is_newline(self):
        """Entering a container puts one child per line."""
        gen = HtmlGen()
        with gen.Div(class_='card'):
            gen.P('a')
            gen.P('b')
        assert gen.generate_str() == """\
<div class="card">
  <p>a</p>
  <p>b</p>
</div>
"""

    def test_explicit_inline_survives_with(self):
        gen = HtmlGen()
        with gen.Div().wrap('inline'):
            gen.Span('a')
            gen.Span('b')
        assert gen.generate_str() == """\
<div><span>a</span><span>b</span></div>
"""

    def test_explicit_newline(self):
        gen = HtmlGen()
        element = gen.Div(Element(gen, 'span', 'a'))
        element.wrap('newline')
        assert gen.generate_str() == """\
<div>
  <span>a</span>
</div>
"""

    def test_auto_short(self):
        gen = HtmlGen()
        gen.P('short').wrap('auto')
        assert gen.generate_str() == """\
<p>short</p>
"""

    def test_auto_long(self):
        """A line that does not fit DEFAULT_WIDTH is broken into lines."""
        gen = HtmlGen()
        gen.P('x' * 200).wrap('auto')
        assert gen.generate_str() == f"""\
<p>
  {"x" * 200}
</p>
"""

    def test_auto_width_of_the_level(self):
        """The columns of the indent count in the width."""
        gen = HtmlGen()
        with gen.Div():
            gen.P('x' * 116).wrap('auto')
        # 2 columns of indent + '<p>' + 116 + '</p>' = 124 > 120
        assert gen.generate_str() == f"""\
<div>
  <p>
    {"x" * 116}
  </p>
</div>
"""

    def test_explicit_width(self):
        gen = HtmlGen()
        gen.P('x' * 30).wrap(20)
        assert gen.generate_str() == f"""\
<p>
  {"x" * 30}
</p>
"""

    def test_nested_indent(self):
        gen = HtmlGen()
        with gen.Ul():
            with gen.Li():
                gen.A('x', href='y')
        assert gen.generate_str() == """\
<ul>
  <li>
    <a href="y">x</a>
  </li>
</ul>
"""

    def test_multiline_text_breaks_inline_parent(self):
        """A child that cannot be one line moves the parent to the block form."""
        gen = HtmlGen()
        gen.Div(Element(gen, 'pre', 'a\nb'))
        assert gen.generate_str() == """\
<div>
  <pre>
    a
    b
  </pre>
</div>
"""

    def test_explicit_newline_child_breaks_inline_parent(self):
        """A child that asks for lines moves the parent to the block form."""
        gen = HtmlGen()
        gen.Div(Element(gen, 'span', 'a').wrap('newline'))
        assert gen.generate_str() == """\
<div>
  <span>
    a
  </span>
</div>
"""

    def test_empty_container_in_newline(self):
        gen = HtmlGen()
        with gen.Div():
            pass
        assert gen.generate_str() == """\
<div>
</div>
"""


class TestChildren:
    def test_attached_child_refused(self):
        """gen.Span() already took the object into the page."""
        gen = HtmlGen()
        with pytest.raises(HtmlError, match='is already in the page'):
            gen.Div(gen.Span('a'))

    def test_detached_child_accepted(self):
        gen = HtmlGen()
        items = [Element(gen, 'li', name) for name in ('a', 'b')]
        gen.Ul(items)
        assert gen.generate_str() == """\
<ul><li>a</li><li>b</li></ul>
"""

    def test_other_generator_refused(self):
        """A child renders with the indent size of the generator that made it."""
        gen = HtmlGen()
        other = HtmlGen()
        with pytest.raises(ValueError, match='belongs to another generator'):
            gen.Div([Element(other, 'span', 'a')])

    def test_child_reindented(self):
        """A child made at the root lands at the level of its new parent."""
        gen = HtmlGen()
        span = Element(gen, 'span', 'a')
        with gen.Div():
            gen.P(span)
        assert gen.generate_str() == """\
<div>
  <p><span>a</span></p>
</div>
"""

    def test_subtree_reindented(self):
        """The children of a moved child keep their depth."""
        gen = HtmlGen()
        inner = Element(gen, 'span', 'a')
        assert inner._indent == 0
        outer = Element(gen, 'div', inner)
        assert inner._indent == 1
        with gen.Div():
            gen.P(outer)
        assert outer._indent == 2
        assert inner._indent == 3

    def test_text_child(self):
        gen = HtmlGen()
        gen.P(Text(gen, 'a < b'))
        assert gen.generate_str() == """\
<p>a &lt; b</p>
"""

    def test_reindent_leaf(self):
        """A leaf has no child to walk, the move is one assignment."""
        gen = HtmlGen()
        text = Text(gen, 'x')
        text._reindent(3)
        assert text._indent == 3
        assert text.indent_str == '      '

    def test_repr(self):
        gen = HtmlGen()
        assert repr(gen.Div()) == '<Element <div>>'


class TestLine:
    def test_one_line(self):
        gen = HtmlGen()
        with gen.Line():
            gen.A('x', href='y')
            gen.Br()
        assert gen.generate_str() == """\
<a href="y">x</a><br>
"""

    def test_inside_container(self):
        gen = HtmlGen()
        with gen.Div():
            with gen.Line():
                gen.A('x', href='y')
                gen.Br()
        assert gen.generate_str() == """\
<div>
  <a href="y">x</a><br>
</div>
"""

    def test_positional_children(self):
        gen = HtmlGen()
        gen.Line(Element(gen, 'b', 'x'), Element(gen, 'i', 'y'))
        assert gen.generate_str() == """\
<b>x</b><i>y</i>
"""

    def test_child_not_on_one_line(self):
        """A line is one line, a fragment of several lines is an error."""
        gen = HtmlGen()
        with gen.Div():
            with gen.Line():
                gen.Raw('a\nb', indent=False)
        with pytest.raises(HtmlError, match='A line holds a fragment'):
            gen.generate_str()

    def test_repr(self):
        gen = HtmlGen()
        assert repr(gen.Line()) == '<Line 0 child(ren)>'
        assert repr(gen.Line(Element(gen, 'b', 'x'))) == '<Line 1 child(ren)>'
