"""
dt="input-line": a single line text box, shown in the vertical layout by default.

The value is a str like dt="input". The frontend edits it with an <input> drawn
in the box style of dt="textarea", so a newline can not be typed and never
breaks the line. A value that already carries a newline is not rewritten here,
the parser stores the str as it is.
"""

from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestInputLine:
    def test_text(self):
        arg = parse_arg("""\
dt: input-line
value: some text
""")
        assert arg.dt == 'input-line'
        assert arg.value == 'some text'
        assert arg.get_anno() == 'str'

    def test_newline_is_kept_in_the_value(self):
        """The line is one row, the stored value is not rewritten."""
        arg = parse_arg("""\
dt: input-line
value: |-
  first
  second
""")
        assert arg.value == 'first\nsecond'

    def test_empty_value(self):
        arg = parse_arg("""\
dt: input-line
value: ""
""")
        assert arg.value == ''

    def test_vert_layout_by_default(self):
        arg = parse_arg("""\
dt: input-line
value: some text
""")
        assert arg.layout == 'vert'

    def test_explicit_layout_is_kept(self):
        arg = parse_arg("""\
dt: input-line
value: some text
layout: hori
""")
        assert arg.layout == 'hori'

    def test_text_constraints(self):
        arg = parse_arg("""\
dt: input-line
value: some text
pattern: '^[a-z ]+$'
max_length: 20
""")
        assert arg.get_anno() == "e.Annotated[str, m.Meta(pattern='^[a-z ]+$', max_length=20)]"
