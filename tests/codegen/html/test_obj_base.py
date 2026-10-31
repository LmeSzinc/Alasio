"""
Tests for the base of the html objects: escaping, attribute names and indent.
"""
import pytest

from alasio.codegen.html import DEFAULT_WIDTH, HtmlError, HtmlGen, escape
from alasio.codegen.html.obj_base import check_tag_name, normalize_attrs, render_attrs


class TestEscape:
    @pytest.mark.parametrize('text, expected', [
        ('', ''),
        ('httpx2', 'httpx2'),
        ('1.0+cu118', '1.0+cu118'),
        ('a & b', 'a &amp; b'),
        ('<script>alert(1)</script>', '&lt;script&gt;alert(1)&lt;/script&gt;'),
        ('say "hi"', 'say &quot;hi&quot;'),
        ("it's", 'it&#x27;s'),
        # the five special characters in one text
        ('<a href="x">&</a>', '&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;'),
    ])
    def test_escape(self, text, expected):
        """The five html special characters become entities."""
        assert escape(text) == expected

    @pytest.mark.parametrize('value, expected', [
        (7, '7'),
        (1.5, '1.5'),
        (True, 'True'),
    ])
    def test_escape_non_text(self, value, expected):
        """A value that is not a text is rendered with str() first."""
        assert escape(value) == expected


class TestCheckTagName:
    @pytest.mark.parametrize('tag, expected', [
        ('p', 'p'),
        ('H1', 'h1'),
        ('Blockquote', 'blockquote'),
        ('my-widget', 'my-widget'),
        ('h1', 'h1'),
    ])
    def test_valid(self, tag, expected):
        assert check_tag_name(tag) == expected

    @pytest.mark.parametrize('tag', ['', ' ', 'a b', 'a>b', '<a', '1a', 'a"', 'a/b'])
    def test_invalid(self, tag):
        with pytest.raises(ValueError, match='Invalid html tag name'):
            check_tag_name(tag)

    @pytest.mark.parametrize('tag', [1, None, b'p'])
    def test_not_a_text(self, tag):
        with pytest.raises(TypeError, match='is not a text'):
            check_tag_name(tag)


class TestNormalizeAttrs:
    def test_order(self):
        """The order of the keyword arguments is the order of the attributes."""
        assert list(normalize_attrs({'href': 'x', 'class_': 'y'})) == ['href', 'class']

    def test_keyword_name(self):
        """A trailing '_' lets a keyword through and is dropped."""
        assert normalize_attrs({'class_': 'card'}) == {'class': 'card'}
        assert normalize_attrs({'for_': 'name'}) == {'for': 'name'}

    def test_underscore_becomes_dash(self):
        assert normalize_attrs({'data_id': 7}) == {'data-id': 7}

    def test_trailing_underscore_then_dash(self):
        """The trailing '_' is dropped first, the rest become '-'."""
        assert normalize_attrs({'http_equiv_': 'refresh'}) == {'http-equiv': 'refresh'}

    def test_lowercase(self):
        assert normalize_attrs({'Data_Id': 7}) == {'data-id': 7}

    def test_boolean_true(self):
        assert normalize_attrs({'hidden': True}) == {'hidden': True}

    @pytest.mark.parametrize('value', [None, False])
    def test_dropped_value(self, value):
        assert normalize_attrs({'hidden': value}) == {}

    @pytest.mark.parametrize('name', ['on click', '', '1x', '<x', 'a/b'])
    def test_invalid_name(self, name):
        with pytest.raises(ValueError, match='Invalid html attribute name'):
            normalize_attrs({name: 'x'})

    def test_duplicate_name(self):
        """class_ and class reach one attribute, the second one is an error."""
        with pytest.raises(ValueError, match='Duplicate html attribute'):
            normalize_attrs({'class_': 'a', 'class': 'b'})

    def test_duplicate_dropped_value(self):
        """The duplicate is refused even when the first value is dropped."""
        with pytest.raises(ValueError, match='Duplicate html attribute'):
            normalize_attrs({'class_': None, 'class': 'b'})

    def test_set_refused(self):
        """A set has no order, the page would change between two runs."""
        with pytest.raises(ValueError, match='is a set'):
            normalize_attrs({'class_': {'a', 'b'}})

    def test_name_not_text(self):
        with pytest.raises(TypeError, match='is not a text'):
            normalize_attrs({1: 'x'})


class TestRenderAttrs:
    def test_empty(self):
        assert render_attrs({}) == ''

    def test_text(self):
        assert render_attrs({'href': 'a/b.html'}) == ' href="a/b.html"'

    def test_escaped(self):
        assert render_attrs({'title': 'a "b" & c'}) == ' title="a &quot;b&quot; &amp; c"'

    def test_boolean(self):
        assert render_attrs({'disabled': True}) == ' disabled'

    def test_list(self):
        assert render_attrs({'class': ['card', 'wide']}) == ' class="card wide"'

    def test_tuple(self):
        assert render_attrs({'class': ('card', 'wide')}) == ' class="card wide"'

    def test_number(self):
        assert render_attrs({'rows': 3}) == ' rows="3"'

    def test_order(self):
        assert render_attrs({'a': '1', 'b': '2'}) == ' a="1" b="2"'


class TestWrap:
    @pytest.mark.parametrize('mode', ['inline', 'newline', 'auto', 40])
    def test_valid(self, mode):
        gen = HtmlGen()
        assert gen.P('x').wrap(mode) is not None

    def test_always_is_newline(self):
        """The name of the python generator keeps its meaning."""
        gen = HtmlGen()
        assert gen.P('x').wrap('always')._wrap == 'newline'

    @pytest.mark.parametrize('mode', ['block', '', None, 0, -1, True, False, 1.5])
    def test_invalid(self, mode):
        gen = HtmlGen()
        with pytest.raises(ValueError, match='wrap mode|must be positive'):
            gen.P('x').wrap(mode)


class TestIndent:
    @pytest.mark.parametrize('indent_size', [1, 2, 4])
    def test_indent_size(self, indent_size):
        gen = HtmlGen(indent_size=indent_size)
        with gen.Div():
            gen.P('x')
        assert gen.generate_str() == f"""\
<div>
{" " * indent_size}<p>x</p>
</div>
"""

    @pytest.mark.parametrize('indent_size', [0, -1])
    def test_invalid_indent_size(self, indent_size):
        with pytest.raises(ValueError, match='indent size must be positive'):
            HtmlGen(indent_size=indent_size)


class TestDefaultWidth:
    def test_default_width(self):
        assert DEFAULT_WIDTH == 120


class TestHtmlError:
    def test_is_exception(self):
        assert issubclass(HtmlError, Exception)
