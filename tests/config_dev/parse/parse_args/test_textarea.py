"""
dt="textarea": multi-line text, shown in the vertical layout by default.
"""

from tests.config_dev.parse.parse_args.helpers import parse_arg


class TestTextarea:
    def test_text(self):
        arg = parse_arg("""\
dt: textarea
value: |-
  multi
  line
""")
        assert arg.value == 'multi\nline'
        assert arg.get_anno() == 'str'

    def test_vert_layout_by_default(self):
        arg = parse_arg("""\
dt: textarea
value: some text
""")
        assert arg.layout == 'vert'

    def test_explicit_layout_is_kept(self):
        arg = parse_arg("""\
dt: textarea
value: some text
layout: hori
""")
        assert arg.layout == 'hori'
