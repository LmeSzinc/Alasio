"""
Tests for the page objects of a pip index: the page of one distribution and one
wheel of it.
"""
import pytest

from alasio.codegen.html import Element, HtmlError, Text
from alasio.codegen.pypisite import PypiSite, Wheel, WheelLink

# the digest of a wheel, the shape the fetcher of the pack server records
DIGEST_A = 'a' * 64
DIGEST_B = 'b' * 64

# the wheels of the tests
FILE_PURE = 'httpx2-2.13.1-py3-none-any.whl'
FILE_WIN = 'httpx2-2.13.1-cp38-cp38-win_amd64.whl'


class TestWheel:
    def test_render(self):
        gen = PypiSite()
        gen.Wheel(WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A))
        assert gen.generate_str() == f"""\
<a href="2.13.1/{FILE_PURE}#sha256={DIGEST_A}">{FILE_PURE}</a><br>
"""

    def test_with_name(self):
        gen = PypiSite()
        gen.Wheel(WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A), with_name=True)
        assert gen.generate_str() == f"""\
<a href="httpx2/2.13.1/{FILE_PURE}#sha256={DIGEST_A}">{FILE_PURE}</a><br>
"""

    def test_without_digest(self):
        gen = PypiSite()
        gen.Wheel(WheelLink('httpx2', '2.13.1', FILE_PURE))
        assert gen.generate_str() == f"""\
<a href="2.13.1/{FILE_PURE}">{FILE_PURE}</a><br>
"""

    def test_one_line_in_a_container(self):
        """The anchor and the line break are one Line, a body keeps them together."""
        gen = PypiSite()
        with gen.Div():
            gen.Wheel(WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A))
        assert gen.generate_str() == f"""\
<div>
  <a href="2.13.1/{FILE_PURE}#sha256={DIGEST_A}">{FILE_PURE}</a><br>
</div>
"""

    def test_text_escaped(self):
        """A file name is a text node of the anchor, so it goes through the escape."""
        gen = PypiSite()
        gen.Wheel(WheelLink('httpx2', '2.13.1', 'a&b.whl', DIGEST_A))
        assert gen.generate_str() == f"""\
<a href="2.13.1/a&amp;b.whl#sha256={DIGEST_A}">a&amp;b.whl</a><br>
"""

    def test_attributes(self):
        gen = PypiSite()
        item = gen.Wheel(WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A))
        assert isinstance(item, Wheel)
        assert item.link.version == '2.13.1'
        assert item.with_name is False
        anchor = item.children[0]
        assert anchor.tag == 'a'
        assert anchor.attrs == {'href': f'2.13.1/{FILE_PURE}#sha256={DIGEST_A}'}
        assert isinstance(anchor.children[0], Text)

    def test_not_a_link(self):
        gen = PypiSite()
        with pytest.raises(TypeError, match='is not a WheelLink'):
            gen.Wheel(('httpx2', '2.13.1', FILE_PURE))

    def test_repr(self):
        gen = PypiSite()
        assert repr(gen.Wheel(WheelLink('httpx2', '2.13.1', FILE_PURE))) == f'<Wheel {FILE_PURE}>'


class TestPypiHeader:
    def test_page(self):
        """The page of one distribution: the simple api version, the heading, the wheels."""
        gen = PypiSite()
        with gen.PypiHeader('httpx2'):
            gen.Wheels([
                WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A),
                WheelLink('httpx2', '2.13.1', FILE_WIN, DIGEST_B),
            ])
        assert gen.generate_str() == f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for httpx2</title>
  </head>
  <body>
    <h1>Links for httpx2</h1>
    <a href="2.13.1/{FILE_WIN}#sha256={DIGEST_B}">{FILE_WIN}</a><br>
    <a href="2.13.1/{FILE_PURE}#sha256={DIGEST_A}">{FILE_PURE}</a><br>
  </body>
</html>
"""

    def test_empty(self):
        gen = PypiSite()
        gen.PypiHeader('httpx2')
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

    def test_project_normalized(self):
        """The title, the heading and the links spell the name one way."""
        gen = PypiSite()
        with gen.PypiHeader('HTTPX2'):
            gen.Wheels([WheelLink('HTTPX2', '2.13.1', FILE_PURE, DIGEST_A)])
        assert gen.generate_str() == f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for httpx2</title>
  </head>
  <body>
    <h1>Links for httpx2</h1>
    <a href="2.13.1/{FILE_PURE}#sha256={DIGEST_A}">{FILE_PURE}</a><br>
  </body>
</html>
"""

    def test_lang(self):
        gen = PypiSite()
        gen.PypiHeader('httpx2', lang='zh-CN')
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="zh-CN">
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

    def test_extra_head(self):
        """An item of the head lands after the simple api version, before the title."""
        gen = PypiSite()
        viewport = Element(gen, 'meta', name='viewport', content='width=device-width')
        gen.PypiHeader('httpx2', head=[viewport])
        assert gen.generate_str() == """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <meta name="viewport" content="width=device-width">
    <title>Links for httpx2</title>
  </head>
  <body>
    <h1>Links for httpx2</h1>
  </body>
</html>
"""

    def test_attached_head_item_refused(self):
        """gen.Meta() already took the object into the page, see Document."""
        gen = PypiSite()
        with pytest.raises(HtmlError, match='is already in the page'):
            gen.PypiHeader('httpx2', head=[gen.Meta(name='viewport', content='x')])

    def test_head_still_reachable(self):
        """The head of the page is one box: a block inside the page adds to it."""
        gen = PypiSite()
        with gen.PypiHeader('httpx2') as page:
            with page.head():
                gen.Meta(name='viewport', content='width=device-width')
            gen.Wheels([WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A)])
        assert gen.generate_str() == f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <meta name="viewport" content="width=device-width">
    <title>Links for httpx2</title>
  </head>
  <body>
    <h1>Links for httpx2</h1>
    <a href="2.13.1/{FILE_PURE}#sha256={DIGEST_A}">{FILE_PURE}</a><br>
  </body>
</html>
"""

    def test_generic_content_goes_to_body(self):
        """The block of the page is its body, so the generic tags need no extra level."""
        gen = PypiSite()
        with gen.PypiHeader('httpx2'):
            with gen.P(class_='note').wrap('inline'):
                gen.Text('Downloaded from the index of the project.')
            gen.Wheels([WheelLink('httpx2', '2.13.1', FILE_PURE, DIGEST_A)])
        assert gen.generate_str() == f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for httpx2</title>
  </head>
  <body>
    <h1>Links for httpx2</h1>
    <p class="note">Downloaded from the index of the project.</p>
    <a href="2.13.1/{FILE_PURE}#sha256={DIGEST_A}">{FILE_PURE}</a><br>
  </body>
</html>
"""

    def test_other_distribution_refused(self):
        """The page sits in the folder of the project, a foreign wheel is a link out."""
        gen = PypiSite()
        with gen.PypiHeader('httpx2'):
            with pytest.raises(ValueError, match='cannot link the wheel of another distribution'):
                gen.Wheels([WheelLink('msgspec', '0.22.0', 'msgspec-0.22.0-py3-none-any.whl')])

    @pytest.mark.parametrize('project', ['', '  ', '../httpx2', 'a/b', 'a b'])
    def test_invalid_project(self, project):
        gen = PypiSite()
        with pytest.raises(ValueError, match='not a distribution name'):
            gen.PypiHeader(project)

    def test_project_restored(self):
        gen = PypiSite()
        with gen.PypiHeader('httpx2'):
            assert gen.project == 'httpx2'
        assert gen.project == ''

    def test_project_restored_after_exception(self):
        gen = PypiSite()
        with pytest.raises(RuntimeError):
            with gen.PypiHeader('httpx2'):
                assert gen.project == 'httpx2'
                raise RuntimeError('boom')
        assert gen.project == ''
        assert gen.context is None

    def test_repr(self):
        gen = PypiSite()
        assert repr(gen.PypiHeader('HTTPX2')) == '<PypiHeader httpx2>'
