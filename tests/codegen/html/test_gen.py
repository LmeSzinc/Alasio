"""
Tests for the generator: the tag methods, the rendering and the write.
"""
import pytest

import alasio.codegen.html.gen_base as gen_base_module
import alasio.codegen.html.write as write_module
from alasio.codegen.html import TAG_TABLE, Comment, Document, Element, HtmlGen, HtmlGenBase, Line, Raw, Text
from alasio.testing.filesystem import fs  # noqa: F401


class FakeGitAdd:
    """The part of GitAdd the generator uses."""

    def __init__(self):
        self.files = []

    def stage_add(self, file):
        self.files.append(file)


class TestTagMethods:
    @pytest.mark.parametrize('tag', sorted(TAG_TABLE))
    def test_method_of_every_tag(self, tag):
        """The tag table is the one source: every tag has its method."""
        gen = HtmlGen()
        item = getattr(gen, tag.capitalize())()
        assert isinstance(item, Element)
        assert item.tag == tag
        assert item.is_void is TAG_TABLE[tag]

    def test_method_attaches(self):
        gen = HtmlGen()
        item = gen.P('x')
        assert gen.items == [item]
        assert item._attached is True

    def test_method_returns_self_object(self):
        gen = HtmlGen()
        assert gen.Div().wrap('inline')._wrap == 'inline'

    def test_method_docstring(self):
        assert 'Define a <p> element' in HtmlGen.P.__doc__
        assert 'takes none' in HtmlGen.Br.__doc__

    def test_text_is_not_the_svg_tag(self):
        """gen.Text is the text node, the svg <text> is written with El."""
        gen = HtmlGen()
        item = gen.Text('a')
        assert isinstance(item, Text)

    def test_el(self):
        gen = HtmlGen()
        gen.El('my-widget', 'a', class_='card')
        assert gen.generate_str() == """\
<my-widget class="card">a</my-widget>
"""

    def test_el_invalid_tag(self):
        gen = HtmlGen()
        with pytest.raises(ValueError, match='Invalid html tag name'):
            gen.El('a b')

    def test_line(self):
        gen = HtmlGen()
        item = gen.Line(Element(gen, 'b', 'x'))
        assert isinstance(item, Line)
        assert gen.generate_str() == """\
<b>x</b>
"""

    def test_comment(self):
        gen = HtmlGen()
        assert isinstance(gen.Comment('x'), Comment)

    def test_raw(self):
        gen = HtmlGen()
        assert isinstance(gen.Raw('<b>x</b>'), Raw)

    def test_doc(self):
        gen = HtmlGen()
        assert isinstance(gen.Doc('T'), Document)


class TestContext:
    def test_items_go_to_the_current_container(self):
        gen = HtmlGen()
        with gen.Div() as div:
            p = gen.P('x')
            assert gen.context is div
            assert div.children == [p]
        assert gen.context is None
        assert gen.items == [div]

    def test_indent_restored_after_exception(self):
        gen = HtmlGen()
        div = gen.Div()
        with pytest.raises(RuntimeError):
            with div:
                raise RuntimeError('boom')
        assert gen.context is None
        assert gen.indent == 0
        gen.P('after')
        assert gen.generate_str() == """\
<div>
</div>
<p>after</p>
"""

    def test_nested_restores_each_level(self):
        gen = HtmlGen()
        with gen.Div():
            outer_indent = gen.indent
            with gen.Span():
                assert gen.indent == outer_indent + 1
            assert gen.indent == outer_indent
        assert gen.indent == 0

    def test_reentered_container(self):
        """The with block of a container in the with block of it restores level by level."""
        gen = HtmlGen()
        div = gen.Div()
        with div:
            with div:
                assert gen.context is div
            assert gen.context is div
        assert gen.context is None
        assert gen.indent == 0

    def test_item_of_another_generator(self):
        gen = HtmlGen()
        other = HtmlGen()
        with pytest.raises(ValueError, match='was made by another one'):
            gen._add_item(other.P('x'))

    def test_item_not_an_object(self):
        gen = HtmlGen()
        with pytest.raises(TypeError, match='is not an html object'):
            gen._add_item('x')


class TestGenerate:
    def test_lines(self):
        """A page is rendered one line at a time, without the trailing newline."""
        gen = HtmlGen()
        with gen.Div():
            gen.P('a')
        assert list(gen.generate()) == ['<div>', '  <p>a</p>', '</div>']

    def test_str(self):
        gen = HtmlGen()
        gen.P('a')
        assert gen.generate_str() == """\
<p>a</p>
"""

    def test_empty(self):
        """A page of nothing is the empty text, a file of nothing is no page."""
        assert HtmlGen().generate_str() == ''

    def test_repeatable(self):
        """Rendering does not change the tree, two renders are the same bytes."""
        gen = HtmlGen()
        with gen.Doc('T') as page:
            with page.body():
                gen.P('a')
        assert gen.generate_str() == gen.generate_str()

    def test_has_content(self):
        gen = HtmlGen()
        assert gen.has_content is False
        gen.P('x')
        assert gen.has_content is True

    def test_print(self, capsys):
        gen = HtmlGen()
        gen.P('a')
        gen.print()
        assert capsys.readouterr().out == """\
<p>a</p>
"""

    def test_repr(self):
        gen = HtmlGen()
        assert repr(gen) == '<HtmlGen 0 item(s)>'
        gen.P('x')
        assert repr(gen) == '<HtmlGen 1 item(s)>'

    def test_base_generator_renders_nothing(self):
        assert HtmlGenBase().generate_str() == ''


class TestWrite:
    def test_write_new(self, fs, monkeypatch):
        gen = HtmlGen()
        gen.P('a')
        assert gen.write('/out/index.html') is True
        with open('/out/index.html', encoding='utf-8') as f:
            assert f.read() == """\
<p>a</p>
"""

    def test_skip_same(self, fs, monkeypatch):
        """The second write of the same page does not touch the file."""
        gen = HtmlGen()
        gen.P('a')
        gen.write('/out/index.html')

        def boom(*args, **kwargs):
            raise AssertionError('the page did not change, the file must not be written')

        monkeypatch.setattr(write_module, 'atomic_write', boom)
        assert gen.write('/out/index.html') is False

    def test_write_changed(self, fs, monkeypatch):
        fs.create_file('/out/index.html', contents='<p>old</p>\n')
        gen = HtmlGen()
        gen.P('a')
        calls = []
        real = write_module.atomic_write

        def record(file, data):
            calls.append((file, data))
            real(file, data)

        monkeypatch.setattr(write_module, 'atomic_write', record)
        assert gen.write('/out/index.html') is True
        (file, data), = calls
        assert file == '/out/index.html'
        assert data == """\
<p>a</p>
"""
        with open('/out/index.html', encoding='utf-8') as f:
            assert f.read() == """\
<p>a</p>
"""

    def test_crlf_file_is_current(self, fs, monkeypatch):
        """A checkout that wrote CRLF holds the same page."""
        fs.create_file('/out/index.html', contents='<p>a</p>\r\n')
        gen = HtmlGen()
        gen.P('a')

        def boom(*args, **kwargs):
            raise AssertionError('the page did not change, the file must not be written')

        monkeypatch.setattr(write_module, 'atomic_write', boom)
        assert gen.write('/out/index.html') is False

    def test_skip_same_false_always_writes(self, fs, monkeypatch):
        fs.create_file('/out/index.html', contents='<p>a</p>\n')
        gen = HtmlGen()
        gen.P('a')
        calls = []
        real = gen_base_module.atomic_write

        def record(file, data):
            calls.append((file, data))
            real(file, data)

        monkeypatch.setattr(gen_base_module, 'atomic_write', record)
        assert gen.write('/out/index.html', skip_same=False) is True
        (file, data), = calls
        assert file == '/out/index.html'
        assert data == """\
<p>a</p>
"""

    def test_parent_folder_created(self, fs, monkeypatch):
        gen = HtmlGen()
        gen.P('a')
        assert gen.write('/deep/inside/out/index.html') is True
        with open('/deep/inside/out/index.html', encoding='utf-8') as f:
            assert f.read() == """\
<p>a</p>
"""

    def test_gitadd_only_on_write(self, fs, monkeypatch):
        gen = HtmlGen()
        gen.P('a')
        gitadd = FakeGitAdd()
        assert gen.write('/out/index.html', gitadd=gitadd) is True
        assert gitadd.files == ['/out/index.html']

        monkeypatch.setattr(write_module, 'atomic_write', lambda *a, **k: None)
        assert gen.write('/out/index.html', gitadd=gitadd) is False
        assert gitadd.files == ['/out/index.html']

    def test_no_gitadd(self, fs):
        gen = HtmlGen()
        gen.P('a')
        assert gen.write('/out/index.html') is True
