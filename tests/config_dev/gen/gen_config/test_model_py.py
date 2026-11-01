"""
Tests of the {nav}_model.py generation (ConfigGenerator.model_gen).

The tests are grouped by the branch of the generator they exercise: the file
header, the class inheritance, the literal variables and their wrapping, the
tuple / literal expansion, the inline args, the cross-nav placement of a class,
the manual methods and the write behaviour.

Every generated source goes through `model_code()`, which compiles it with
compile(), so a branch that emits invalid python fails here instead of when the
generated file is imported. The cases that must be importable are additionally
executed, which also catches a name that is not defined (compile() only checks
the syntax).
"""
import typing as t
from types import SimpleNamespace

import msgspec
import pytest

from alasio.codegen.python.obj_base import GatherItems
from alasio.config.alasio.group_base import DEFAULT_TIME
from alasio.config_dev.parse import parse_groups
from alasio.config_dev.parse.base import DefinitionError
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import GitAddSpy, make_alasio_generator, make_cross, make_generator

HEADER_MOD = """\
import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

"""

HEADER_ALASIO = """\
import typing as t

import alasio.config.alasio.group_base as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m alasio.config_dev.gen_alasio ```

"""

# Width of the collapsed literals and tuples, see GatherItems.DEFAULT_WIDTH
WIDTH = GatherItems.DEFAULT_WIDTH

# 25 characters each, so an expanded row holds three items at WIDTH
ITEM_A = 'AAAA' + '0' * 21
ITEM_B = 'BBBB' + '1' * 21
ITEM_C = 'CCCC' + '2' * 21
ITEM_D = 'DDDD' + '3' * 21


def model_code(gen, filename='testnav_model.py'):
    """
    Generate the model source and check it compiles.

    Every test asserts on the return value, so no branch can emit invalid
    python without failing the suite.

    Args:
        gen (ConfigGenerator): Generator to render
        filename (str): Filename reported by a SyntaxError

    Returns:
        str: Generated source
    """
    code = gen.model_gen.generate_str()
    compile(code, filename, 'exec')
    return code


def definitions(code):
    """
    Strip the import block and the banner, keeping the generated definitions.

    Args:
        code (str): Generated source

    Returns:
        str: Everything after the auto-generated banner
    """
    return code.split('```\n\n', 1)[1]


class TestModelFileHeader:
    def test_mod_nav_header(self, fs):
        """A nav of a mod imports the group classes of group_export."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode: normal
""")
        assert model_code(gen) == HEADER_MOD + """\
class Group(a.GroupBase):
    Mode: str = 'normal'
"""

    def test_alasio_nav_header(self, fs):
        """A nav of the alasio entry imports the group classes of group_base."""
        gen = make_alasio_generator(fs, nav='device', yaml="""\
Device:
  args:
    Serial: 127.0.0.1:5555
""")
        assert model_code(gen) == HEADER_ALASIO + """\
class Device(a.GroupBase):
    Serial: str = '127.0.0.1:5555'
"""

    def test_no_content_no_file(self, fs):
        """A nav without any arg has no model to generate."""
        gen = make_generator(fs, yaml="""\
Info:

Empty:
""")
        assert gen.model_gen is None


class TestInheritName:
    def test_plain_parent(self, fs):
        """A group of the same mod is referenced by its class name."""
        gen = make_generator(fs)
        assert gen.get_inherit_name('Base') == 'Base'

    def test_dashboard_parent(self, fs):
        """A Dashboard group is exported by group_export."""
        gen = make_generator(fs)
        assert gen.get_inherit_name('DashboardTotal') == 'a.DashboardTotal'

    def test_alasio_group_parent(self, fs):
        """A group of the alasio entry is exported by group_export."""
        gen = make_generator(fs)
        gen.alasio = SimpleNamespace(groups_data={'AlasioBase': object()})
        assert gen.get_inherit_name('AlasioBase') == 'a.AlasioBase'

    def test_alasio_group_parent_not_loaded(self, fs):
        """An unknown parent is referenced by its class name."""
        gen = make_generator(fs)
        gen.alasio = SimpleNamespace(groups_data={})
        assert gen.get_inherit_name('AlasioBase') == 'AlasioBase'


class TestModelFileInheritance:
    def test_variant_inherits_base_in_same_file(self, fs):
        """A variant inherits the class of its base group."""
        gen = make_generator(fs, yaml="""\
Base:
  args:
    X: 1

Child:
  parent: Base
  args:
    Y: 2
""")
        assert definitions(model_code(gen)) == """\
class Base(a.GroupBase):
    X: int = 1


class Child(Base):
    Y: int = 2
"""

    def test_multiple_parents(self, fs):
        """The parent list is kept in order."""
        gen = make_generator(fs, yaml="""\
A:
  args:
    X: 1

B:
  args:
    Y: 2

C:
  parent: [A, B]
  args:
    Z: 3
""")
        assert definitions(model_code(gen)) == """\
class A(a.GroupBase):
    X: int = 1


class B(a.GroupBase):
    Y: int = 2


class C(A, B):
    Z: int = 3
"""

    def test_variant_without_override_args_is_pass(self, fs):
        """A variant that overrides nothing gets an empty class body."""
        gen = make_cross(fs, {
            'nava': """\
Base:
  args:
    X: 1

Child:
  parent: Base
""",
        })
        code = model_code(gen.dict_nav_config['nava'])
        assert definitions(code) == """\
class Base(a.GroupBase):
    X: int = 1


class Child(Base):
    pass
"""

    def test_dashboard_group(self, fs):
        """A dashboard group inherits the exported Dashboard class."""
        gen = make_generator(fs, yaml="""\
Oil:
  parent: DashboardTotal
  args:
    Value:
      value: 0
      range: 0~100
""")
        group = gen.groups_data['Oil']
        group.dashboard = 'Total'
        group.populate_dashboard()
        assert definitions(model_code(gen)) == """\
class Oil(a.DashboardTotal):
    Value: e.Annotated[int, m.Meta(ge=0, le=100)] = 0
"""

    def test_dashboard_arg_without_range(self, fs):
        """A dashboard arg without range gets the ge=0 alias."""
        gen = make_generator(fs, yaml="""\
Oil:
  parent: DashboardTotal
  args:
    Value: 0
""")
        group = gen.groups_data['Oil']
        group.dashboard = 'Total'
        group.populate_dashboard()
        assert definitions(model_code(gen)) == """\
class Oil(a.DashboardTotal):
    Value: a.T_INT_GE0 = 0
"""

    def test_parent_defined_later_in_file(self, fs):
        """A parent declared after the group inheriting it is rejected."""
        with pytest.raises(DefinitionError) as e:
            make_cross(fs, {
                'testnav': """\
Child:
  parent: Base
  args:
    Z: 3

Base:
  args:
    X: 1
""",
            })
        assert e.value.msg == 'Group parent must be defined before the group: "Base"'
        assert e.value.keys == ['Child', 'parent']
        assert e.value.value == 'Base'

    def test_yaml_order_is_kept(self, fs):
        """The models are generated in the order the yaml declares them."""
        gen = make_cross(fs, {
            'testnav': """\
Base:
  args:
    X: 1

Zeta:
  args:
    A: 1

Middle:
  parent: Base
  args:
    C: 3
""",
        })
        code = model_code(gen.dict_nav_config['testnav'])
        assert code.index('class Base(') < code.index('class Zeta(') < code.index('class Middle(')


class TestModelFileCrossNav:
    def test_parent_in_other_nav_is_imported(self, fs):
        """The class stays in its own nav, the base of another nav is imported."""
        gen = make_cross(fs, {
            'main': """\
Campaign:
  args:
    Name: main
""",
            'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name: gems
""",
        })
        assert definitions(model_code(gen.dict_nav_config['gems'], filename='gems_model.py')) == """\
if t.TYPE_CHECKING or 'Campaign' not in globals():
    from ..main.main_model import Campaign


class GemsCampaign(Campaign):
    Name: str = 'gems'
"""

    def test_bases_of_one_nav_are_one_import(self, fs):
        """Every base of the same nav is imported with one statement."""
        gen = make_cross(fs, {
            'main': """\
Campaign:
  args:
    Name: main

StopCondition:
  args:
    Count: 1
""",
            'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name: gems

GemsStopCondition:
  parent: StopCondition
  args:
    Count: 3
""",
        })
        assert definitions(model_code(gen.dict_nav_config['gems'], filename='gems_model.py')) == """\
if t.TYPE_CHECKING or 'Campaign' not in globals():
    from ..main.main_model import Campaign, StopCondition


class GemsCampaign(Campaign):
    Name: str = 'gems'


class GemsStopCondition(StopCondition):
    Count: int = 3
"""

    def test_parents_in_two_navs(self, fs):
        """A nav gets one guarded import per model file it inherits from."""
        gen = make_cross(fs, {
            'main': """\
Campaign:
  args:
    Name: main
""",
            'other': """\
Fleet:
  args:
    Fleet: 1
""",
            'gems': """\
GemsCampaign:
  parent: [Campaign, Fleet]
  args:
    Name: gems
""",
        })
        assert definitions(model_code(gen.dict_nav_config['gems'], filename='gems_model.py')) == """\
if t.TYPE_CHECKING or 'Campaign' not in globals():
    from ..main.main_model import Campaign


if t.TYPE_CHECKING or 'Fleet' not in globals():
    from ..other.other_model import Fleet


class GemsCampaign(Campaign, Fleet):
    Name: str = 'gems'
"""

    def test_model_module_relative_path(self, fs):
        """The import module points from the model file of one nav to another."""
        gen = make_cross(fs, {
            'main': """\
Campaign:
  args:
    Name: main
""",
            'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name: gems
""",
        })
        assert gen.dict_nav_config['gems'].model_module('main') == '..main.main_model'
        assert gen.dict_nav_config['main'].model_module('gems') == '..gems.gems_model'

    def test_parent_imports_is_grouped_and_sorted(self, fs):
        """The bases are grouped by declaring nav, the navs are sorted."""
        gen = make_cross(fs, {
            'zeta': """\
Zeta:
  args:
    Z: 1
""",
            'main': """\
Campaign:
  args:
    Name: main

StopCondition:
  args:
    Count: 1
""",
            'gems': """\
GemsCampaign:
  parent: [Campaign, Zeta]
  args:
    Name: gems

GemsStopCondition:
  parent: StopCondition
  args:
    Count: 3
""",
        })
        assert gen.dict_nav_config['gems'].parent_imports == {
            'main': ['Campaign', 'StopCondition'],
            'zeta': ['Zeta'],
        }
        # a parent of the same nav is not an import
        assert gen.dict_nav_config['main'].parent_imports == {}

    def test_injected_base_is_not_imported(self, fs):
        """A loader injecting the base skips the import and shares the class."""
        gen = make_cross(fs, {
            'main': """\
Campaign:
  args:
    Name: main
""",
            'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name: gems
""",
        })
        namespace_main = {}
        code_main = model_code(gen.dict_nav_config['main'], filename='main_model.py')
        exec(compile(code_main, 'main_model.py', 'exec'), namespace_main)

        # the base class injected by the loader, the import must not run
        namespace = {'Campaign': namespace_main['Campaign']}
        code_gems = model_code(gen.dict_nav_config['gems'], filename='gems_model.py')
        exec(compile(code_gems, 'gems_model.py', 'exec'), namespace)

        assert namespace['GemsCampaign'].__mro__[1] is namespace_main['Campaign']
        assert namespace['GemsCampaign']().Name == 'gems'

    def test_import_runs_without_the_injected_base(self, fs):
        """A plain import takes the guarded branch and imports the base."""
        gen = make_cross(fs, {
            'main': """\
Campaign:
  args:
    Name: main
""",
            'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name: gems
""",
        })
        code = model_code(gen.dict_nav_config['gems'], filename='gems_model.py')
        # the module is not executed as a package member, so the relative
        # import is attempted and fails, which proves the branch was taken
        with pytest.raises(ImportError):
            exec(compile(code, 'gems_model.py', 'exec'), {'__name__': 'gems_model'})

    def test_inherited_empty_group_gets_a_class(self, fs):
        """A parent without args has no field, but its child needs the class."""
        gen = make_generator(fs, yaml="""\
Base:

Child:
  parent: Base
  args:
    X: 1
""")
        assert definitions(model_code(gen)) == """\
class Base(a.GroupBase):
    pass


class Child(Base):
    X: int = 1
"""

    def test_model_index_points_to_the_declaring_nav(self, fs):
        """The loader reads a class from the nav declaring the group."""
        gen = make_cross(fs, {
            'main': """\
Campaign:
  args:
    Name: main
""",
            'gems': """\
GemsCampaign:
  parent: Campaign
  args:
    Name: gems
""",
        })
        assert gen.dict_group_ref['Campaign'] == {'file': 'main/main_model.py', 'cls': 'Campaign'}
        assert gen.dict_group_ref['GemsCampaign'] == {'file': 'gems/gems_model.py', 'cls': 'GemsCampaign'}


class TestNavModelCycle:
    def test_cycle_between_two_navs(self, fs):
        """Two navs importing the model of each other are rejected."""
        with pytest.raises(DefinitionError) as e:
            make_cross(fs, {
                'nava': """\
A:
  args:
    X: 1

NA:
  parent: B
  args:
    Y: 2
""",
                'navb': """\
B:
  args:
    X: 1

NB:
  parent: A
  args:
    Z: 3
""",
            })
        assert e.value.msg == 'Circular model inheritance between navs: nava -> navb -> nava'
        assert e.value.keys == ['NB', 'parent']
        assert e.value.value == 'nava'
        assert e.value.file.endswith('navb/navb.args.yaml')

    def test_cycle_between_three_navs(self, fs):
        """The whole path of a longer cycle is reported."""
        with pytest.raises(DefinitionError) as e:
            make_cross(fs, {
                'nava': """\
A:
  args:
    X: 1

NA:
  parent: B
  args:
    Y: 2
""",
                'navb': """\
B:
  args:
    X: 1

NB:
  parent: C
  args:
    Y: 2
""",
                'navc': """\
C:
  args:
    X: 1

NC:
  parent: A
  args:
    Y: 2
""",
            })
        assert e.value.msg == (
            'Circular model inheritance between navs: nava -> navb -> navc -> nava'
        )

    def test_same_nav_parent_is_not_a_cycle(self, fs):
        """A parent of the same nav is not an edge between navs."""
        gen = make_cross(fs, {
            'nava': """\
Base:
  args:
    X: 1

Child:
  parent: Base
  args:
    Y: 2
""",
        })
        assert gen.dict_nav_model_parents == {}

    def test_diamond_is_not_a_cycle(self, fs):
        """Two navs inheriting the same nav is allowed."""
        gen = make_cross(fs, {
            'nava': """\
A:
  args:
    X: 1

NA:
  parent: [B, C]
  args:
    Y: 2
""",
            'navb': """\
B:
  parent: D
  args:
    X: 1
""",
            'navc': """\
C:
  parent: D
  args:
    X: 1
""",
            'navd': """\
D:
  args:
    X: 1
""",
        })
        assert gen.dict_nav_config['nava'].parent_imports == {'navb': ['B'], 'navc': ['C']}

    def test_cycle_behind_a_clean_nav(self, fs):
        """A cycle is reported even when the walk starts outside of it."""
        with pytest.raises(DefinitionError) as e:
            make_cross(fs, {
                'nava': """\
A:
  args:
    X: 1

NA:
  parent: B
  args:
    Y: 2
""",
                'navb': """\
B:
  args:
    X: 1

NB:
  parent: C
  args:
    Y: 2
""",
                'navc': """\
C:
  args:
    X: 1

NC:
  parent: B
  args:
    Y: 2
""",
            })
        assert e.value.msg == 'Circular model inheritance between navs: navb -> navc -> navb'


class TestLiteralVariable:
    @pytest.mark.parametrize('dt', ['select', 'radio'])
    def test_literal_dt(self, fs, dt):
        """A select-like arg has one module level literal variable."""
        gen = make_generator(fs, yaml=f"""\
Group:
  args:
    Mode:
      dt: {dt}
      value: normal
      option: [normal, hard]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Mode = t.Literal['normal', 'hard']


class Group(a.GroupBase):
    Mode: LITERAL_Group_Mode = 'normal'
"""

    def test_static_hidden(self, fs):
        """static-hide is a literal arg like static."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode:
      dt: static-hide
      value: campaign_main
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Mode = t.Literal['campaign_main']


class Group(a.GroupBase):
    Mode: LITERAL_Group_Mode = 'campaign_main'
"""

    def test_secondary_select_option_dict(self, fs):
        """The literal of a secondary-select is the flattened option groups."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Table:
      dt: secondary-select
      value: 1-1
      option_dict:
        chapter1: [1-1, 1-2]
        chapter2: [2-1, 2-2]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Table = t.Literal['1-1', '1-2', '2-1', '2-2']


class Group(a.GroupBase):
    Table: LITERAL_Group_Table = '1-1'
"""

    def test_secondary_select_option_as_dict(self, fs):
        """A secondary-select accepts "option" written like "option_dict"."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Table:
      dt: secondary-select
      value: 1-1
      option:
        chapter1: [1-1, 1-2]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Table = t.Literal['1-1', '1-2']


class Group(a.GroupBase):
    Table: LITERAL_Group_Table = '1-1'
"""

    def test_reuse_in_same_group(self, fs):
        """Two args of one group with the same options share one variable."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Mode:
      value: normal
      option: [normal, hard]
    Same:
      value: hard
      option: [normal, hard]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Mode = t.Literal['normal', 'hard']


class Group(a.GroupBase):
    Mode: LITERAL_Group_Mode = 'normal'
    Same: LITERAL_Group_Mode = 'hard'
"""

    def test_reuse_across_groups(self, fs):
        """The variable of the first group is reused by a later group."""
        gen = make_generator(fs, yaml="""\
GroupA:
  args:
    Mode:
      value: a
      option: [a, b]

GroupB:
  args:
    Kind:
      value: a
      option: [a, b]
    Other:
      value: c
      option: [c, d]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_GroupA_Mode = t.Literal['a', 'b']


class GroupA(a.GroupBase):
    Mode: LITERAL_GroupA_Mode = 'a'


LITERAL_GroupB_Other = t.Literal['c', 'd']


class GroupB(a.GroupBase):
    Kind: LITERAL_GroupA_Mode = 'a'
    Other: LITERAL_GroupB_Other = 'c'
"""

    def test_reuse_with_filter_order(self, fs):
        """A filter-order arg emits the variable, a select with the same options reuses it."""
        gen = make_generator(fs, yaml="""\
G:
  args:
    Order:
      dt: filter-order
      value: B > A
      option: [A, B]
    Mode:
      value: A
      option: [A, B]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_G_Order = t.Literal['A', 'B']


class G(a.GroupBase):
    Order: t.Tuple[LITERAL_G_Order, ...] = ('B', 'A')
    Mode: LITERAL_G_Order = 'A'
"""

    def test_distinct_literals(self, fs):
        """Different options get different variables."""
        gen = make_generator(fs, yaml="""\
GroupA:
  args:
    Mode:
      value: a
      option: [a, b]

GroupB:
  args:
    Mode:
      value: c
      option: [c, d]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_GroupA_Mode = t.Literal['a', 'b']


class GroupA(a.GroupBase):
    Mode: LITERAL_GroupA_Mode = 'a'


LITERAL_GroupB_Mode = t.Literal['c', 'd']


class GroupB(a.GroupBase):
    Mode: LITERAL_GroupB_Mode = 'c'
"""

    def test_int_and_bool_are_distinct(self, fs):
        """The reuse key is the repr of the items, 1 and True do not conflate."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Int:
      value: 1
      option: [1, 0]
    Bool:
      value: true
      option: [true, false]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Int = t.Literal[1, 0]
LITERAL_Group_Bool = t.Literal[True, False]


class Group(a.GroupBase):
    Int: LITERAL_Group_Int = 1
    Bool: LITERAL_Group_Bool = True
"""

    def test_empty_group_is_skipped(self, fs):
        """An inforef group has no class and reserves no literal name."""
        gen = make_generator(fs, yaml="""\
EmptyGroup:

FullGroup:
  args:
    Mode:
      value: normal
      option: [normal, hard]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_FullGroup_Mode = t.Literal['normal', 'hard']


class FullGroup(a.GroupBase):
    Mode: LITERAL_FullGroup_Mode = 'normal'
"""

    def test_cross_file_independence(self, fs):
        """The same literal of two navs is defined in each model file."""
        yaml = """\
Group:
  args:
    Mode:
      value: normal
      option: [normal, hard]
"""
        code_a = model_code(make_generator(fs, nav='nava', yaml=yaml), filename='nava_model.py')
        code_b = model_code(make_generator(fs, nav='navb', yaml=yaml), filename='navb_model.py')
        assert "LITERAL_Group_Mode = t.Literal['normal', 'hard']" in code_a
        assert definitions(code_a) == definitions(code_b)

    def test_option_quoting(self, fs):
        """The items are rendered with repr, quotes and unicode survive."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Quote:
      value: "it's here"
      option:
        - "it's here"
        - 'say "hi"'
        - 主线
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Quote = t.Literal["it's here", 'say "hi"', '主线']


class Group(a.GroupBase):
    Quote: LITERAL_Group_Quote = "it's here"
"""


class TestLiteralWrap:
    def test_option_dict_rows(self, fs):
        """A long option_dict is expanded with one row per option group."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Table:
      dt: secondary-select
      value: chapter1-1
      option_dict:
        chapter1: [chapter1-1, chapter1-2, chapter1-3, chapter1-4]
        chapter2: [chapter2-1, chapter2-2, chapter2-3, chapter2-4]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Table = t.Literal[
    'chapter1-1', 'chapter1-2', 'chapter1-3', 'chapter1-4',
    'chapter2-1', 'chapter2-2', 'chapter2-3', 'chapter2-4',
]


class Group(a.GroupBase):
    Table: LITERAL_Group_Table = 'chapter1-1'
"""

    @pytest.mark.parametrize('overflow, expanded', [(0, False), (1, True)], ids=['fits', 'overflow'])
    def test_width_boundary(self, fs, overflow, expanded):
        """The literal stays inline at exactly DEFAULT_WIDTH, expands one char later."""
        prefix = 'LITERAL_Group_Width = t.Literal'
        # '[' + repr(item) + ']' after the prefix
        item = 'x' * (WIDTH - len(prefix) - 2 - 2 + overflow)
        assert len(f'{prefix}[{item!r}]') == WIDTH + overflow
        gen = make_generator(fs, yaml=f"""\
Group:
  args:
    Width:
      value: {item}
      option: [{item}]
""")
        lines = definitions(model_code(gen)).splitlines()
        if expanded:
            assert lines[:3] == [
                f'{prefix}[',
                f'    {item!r},',
                ']',
            ]
        else:
            assert lines[:1] == [f'{prefix}[{item!r}]']

    def test_single_option_longer_than_width(self, fs):
        """An item that does not fit alone is emitted on an overlong row."""
        prefix = 'LITERAL_Group_Long = t.Literal'
        item = 'y' * (WIDTH + 10)
        gen = make_generator(fs, yaml=f"""\
Group:
  args:
    Long:
      value: {item}
      option: [{item}]
""")
        lines = definitions(model_code(gen)).splitlines()
        assert lines[:3] == [
            f'{prefix}[',
            f'    {item!r},',
            ']',
        ]
        assert len(lines[1]) > WIDTH

    def test_rows_are_packed_at_width(self, fs):
        """A long select is expanded with the items packed at DEFAULT_WIDTH."""
        gen = make_generator(fs, yaml=f"""\
G:
  args:
    Mode:
      value: {ITEM_A}
      option: [{ITEM_A}, {ITEM_B}, {ITEM_C}, {ITEM_D}]
""")
        # the literal is a module level variable, its items are indented by 4
        # spaces only, so the 4 items fit one row of 116 usable characters
        assert definitions(model_code(gen)) == f"""\
LITERAL_G_Mode = t.Literal[
    {ITEM_A!r}, {ITEM_B!r}, {ITEM_C!r}, {ITEM_D!r},
]


class G(a.GroupBase):
    Mode: LITERAL_G_Mode = {ITEM_A!r}
"""


class TestLiteralNameConflict:
    def test_group_arg_name_conflict(self, fs, monkeypatch):
        """
        The joined name of two group.arg pairs may collide, e.g.
        "A_B"."C" and "A"."B_C" both want "LITERAL_A_B_C".

        Group and arg names can not contain "_" today, the name validator is
        patched to simulate a relaxed rule.
        """
        monkeypatch.setattr(parse_groups, 'validate_task_name', lambda name: True)
        gen = make_generator(fs, yaml="""\
A:
  args:
    B_C:
      value: x
      option: [x, y]

A_B:
  args:
    C:
      value: 1
      option: [1, 2]
""")
        with pytest.raises(DefinitionError) as e:
            _ = gen.model_gen
        assert 'Literal variable name conflict: "LITERAL_A_B_C"' in str(e.value)
        assert "keys=['A_B', 'C']" in str(e.value)


class TestTupleArg:
    def test_inline(self, fs):
        """A short filter value is an inline tuple."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Free:
      dt: filter
      value: A > B
""")
        assert definitions(model_code(gen)) == """\
class Group(a.GroupBase):
    Free: a.T_TUPLE_STR = ('A', 'B')
"""

    def test_single_value_keeps_trailing_comma(self, fs):
        """A one item tuple must keep its comma, (item) is not a tuple."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Free:
      dt: filter
      value: only
""")
        assert definitions(model_code(gen)) == """\
class Group(a.GroupBase):
    Free: a.T_TUPLE_STR = ('only',)
"""

    def test_empty_value(self, fs):
        """An empty filter value is an empty tuple."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Free:
      dt: filter
      value: []
""")
        assert definitions(model_code(gen)) == """\
class Group(a.GroupBase):
    Free: a.T_TUPLE_STR = ()
"""

    def test_expand(self, fs):
        """A filter value longer than the width is expanded with a trailing comma."""
        gen = make_generator(fs, yaml=f"""\
Group:
  args:
    Free:
      dt: filter
      value: {ITEM_A} > {ITEM_B} > {ITEM_C} > {ITEM_D}
""")
        assert definitions(model_code(gen)) == f"""\
class Group(a.GroupBase):
    Free: a.T_TUPLE_STR = (
        {ITEM_A!r}, {ITEM_B!r}, {ITEM_C!r},
        {ITEM_D!r},
    )
"""

    @pytest.mark.parametrize('overflow, expanded', [(0, False), (1, True)], ids=['fits', 'overflow'])
    def test_width_boundary(self, fs, overflow, expanded):
        """The tuple stays inline at exactly DEFAULT_WIDTH, expands one char later."""
        prefix = '    Free: a.T_TUPLE_STR = ('
        # repr(item) + ',)' after the prefix
        item = 'x' * (WIDTH - len(prefix) - len(',)') - 2 + overflow)
        assert len(f'{prefix}{item!r},)') == WIDTH + overflow
        gen = make_generator(fs, yaml=f"""\
Group:
  args:
    Free:
      dt: filter
      value: [{item}]
""")
        lines = definitions(model_code(gen)).splitlines()
        if expanded:
            assert lines[1:4] == [
                '    Free: a.T_TUPLE_STR = (',
                f'        {item!r},',
                '    )',
            ]
        else:
            assert lines[1:2] == [f'{prefix}{item!r},)']

    def test_filter_order_annotation(self, fs):
        """A filter-order value is a tuple of the literal items."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Order:
      dt: filter-order
      value: B > A
      option: [A, B, C]
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Order = t.Literal['A', 'B', 'C']


class Group(a.GroupBase):
    Order: t.Tuple[LITERAL_Group_Order, ...] = ('B', 'A')
"""

    def test_filter_order_expand(self, fs):
        """Both the literal and the tuple of a long filter-order are expanded."""
        items = [f'OPT{i}' + 'y' * 21 for i in range(8)]
        gen = make_generator(fs, yaml=f"""\
G:
  args:
    Order:
      dt: filter-order
      value: {' > '.join(items[:6])}
      option: [{', '.join(items)}]
""")
        # the literal items are indented by 4 spaces (116 usable), the tuple
        # items by 8 (112 usable), so the literal holds 4 items per row and the
        # tuple 3 items per row
        assert definitions(model_code(gen)) == f"""\
LITERAL_G_Order = t.Literal[
    {items[0]!r}, {items[1]!r}, {items[2]!r}, {items[3]!r},
    {items[4]!r}, {items[5]!r}, {items[6]!r}, {items[7]!r},
]


class G(a.GroupBase):
    Order: t.Tuple[LITERAL_G_Order, ...] = (
        {items[0]!r}, {items[1]!r}, {items[2]!r},
        {items[3]!r}, {items[4]!r}, {items[5]!r},
    )
"""


class TestInlineArg:
    def test_scalar_types(self, fs):
        """An arg without literal keeps its field inline."""
        gen = make_generator(fs, yaml="""\
G:
  args:
    Text: hello
    Area:
      dt: textarea
      value: |
        line1
        line2
    Int: 3
    Float: 1.5
    Flag: true
    Enable:
      dt: enable
      value: false
""")
        assert definitions(model_code(gen)) == """\
class G(a.GroupBase):
    Text: str = 'hello'
    Area: str = 'line1\\nline2\\n'
    Int: int = 3
    Float: float = 1.5
    Flag: bool = True
    Enable: bool = False
"""

    def test_meta_range(self, fs):
        """A numeric range becomes msgspec.Meta."""
        gen = make_generator(fs, yaml="""\
G:
  args:
    Count:
      value: 3
      range: 1~10
""")
        assert definitions(model_code(gen)) == """\
class G(a.GroupBase):
    Count: e.Annotated[int, m.Meta(ge=1, le=10)] = 3
"""

    def test_datetime_alias(self, fs):
        """A datetime uses the DEFAULT_TIME alias, the value is static."""
        gen = make_generator(fs, yaml="""\
G:
  args:
    Start:
      dt: datetime
      value: 2020-01-01 00:00:00
""")
        assert definitions(model_code(gen)) == """\
class G(a.GroupBase):
    Start: a.T_DATETIME = a.DEFAULT_TIME
"""

    def test_quotes_in_value(self, fs):
        """The default value is rendered with repr."""
        gen = make_generator(fs, yaml="""\
G:
  args:
    Quote:
      value: "it's here"
    Other:
      value: 'say "hi"'
""")
        assert definitions(model_code(gen)) == """\
class G(a.GroupBase):
    Quote: str = "it's here"
    Other: str = 'say "hi"'
"""

    def test_hidden_arg_is_in_the_model(self, fs):
        """A hidden arg is still a field of the model, it is only hidden on the UI."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    Shown: 1
    Hidden:
      dt: static-hide
      value: x
""")
        assert definitions(model_code(gen)) == """\
LITERAL_Group_Hidden = t.Literal['x']


class Group(a.GroupBase):
    Shown: int = 1
    Hidden: LITERAL_Group_Hidden = 'x'
"""


MODEL_WITH_METHODS = '''\
class Group:
    @property
    def label(self):
        """A docstring."""
        return self.X

    def set(self, value):
        self.X = value
'''


class TestModelFileMethods:
    def test_no_model_file(self, fs):
        """A nav generated for the first time has no manual method."""
        gen = make_generator(fs, yaml="Group:\n  args:\n    X: 1\n")
        assert gen.model_methods == {}

    def test_methods_are_preserved(self, fs):
        """Manual methods of a generated class are copied to the new file."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    X: 1
""", model=MODEL_WITH_METHODS)
        assert gen.model_methods == {
            'Group': {
                'label': [
                    '    @property',
                    '    def label(self):',
                    '        """A docstring."""',
                    '        return self.X',
                ],
                'set': [
                    '    def set(self, value):',
                    '        self.X = value',
                ],
            },
        }
        assert definitions(model_code(gen)) == '''\
class Group(a.GroupBase):
    X: int = 1

    @property
    def label(self):
        """A docstring."""
        return self.X

    def set(self, value):
        self.X = value
'''

    def test_methods_of_removed_class_are_dropped(self, fs):
        """The methods of a class that is no longer defined are not copied."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    X: 1
""", model="""\
class Removed:
    def run(self):
        return 1
""")
        assert definitions(model_code(gen)) == """\
class Group(a.GroupBase):
    X: int = 1
"""

    def test_methods_of_group_without_args_are_kept(self, fs):
        """A group without args keeps its class and the manual methods of it."""
        gen = make_generator(fs, yaml="""\
Mixin:
Group:
  args:
    X: 1
""", model="""\
class Mixin:
    def run(self):
        return 1

class Group:
    def run(self):
        return 2
""")
        assert definitions(model_code(gen)) == """\
class Mixin(a.GroupBase):
    def run(self):
        return 1


class Group(a.GroupBase):
    X: int = 1

    def run(self):
        return 2
"""

    def test_mixin_nav_model_is_not_generated(self, fs):
        """The model of the mixin nav is manually maintained, like the store nav."""
        gen = make_alasio_generator(fs, nav='mixin', yaml="""\
Future12hMixin:
  args:
    NextRun:
      dt: datetime
      value: 2020-01-01 00:00:00
""")
        model = """\
class _FutureMixinBase:
    def limit_future(self, delta):
        return delta


class Future12hMixin(_FutureMixinBase):
    def post_edit(self, old, edits):
        return self.limit_future(12)
"""
        fs.create_file(gen.model_file, contents=model)
        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert capture.concat() == ''
        assert gen.model_file.atomic_read_text() == model

    def test_mixin_nav_model_is_not_removed(self, fs):
        """The model of the mixin nav survives an empty {nav}.args.yaml.

        The other navs lose the model of a nav whose groups are all removed,
        the mixin nav keeps its hand-written classes.
        """
        gen = make_alasio_generator(fs, nav='mixin', yaml='')
        model = """\
class _FutureMixinBase:
    def limit_future(self, delta):
        return delta
"""
        fs.create_file(gen.model_file, contents=model)
        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert 'Delete file' not in capture.concat()
        assert gen.model_file.atomic_read_text() == model

    def test_syntax_error_in_model_file(self, fs):
        """A broken manual method is reported instead of dropped."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    X: 1
""", model="""\
class Group(:
    pass
""")
        with pytest.raises(DefinitionError) as e:
            _ = gen.model_gen
        assert 'SyntaxError in model file' in str(e.value)
        assert str(gen.model_file) in str(e.value)


class TestModelFileWrite:
    def test_generate_writes_model_file(self, fs):
        """generate() writes the model next to the args yaml."""
        gen = make_generator(fs, yaml="""\
Group:
  args:
    X: 1
""")
        with logger.mock_capture_writer() as capture:
            gen.generate()
        content = gen.model_file.atomic_read_text()
        compile(content, 'testnav_model.py', 'exec')
        assert content == HEADER_MOD + """\
class Group(a.GroupBase):
    X: int = 1
"""
        assert capture.fd.any_contains(f'Write file {gen.model_file}')

    def test_unchanged_model_file_is_not_written(self, fs):
        """The second generation of the same yaml writes nothing."""
        make_generator(fs, yaml="""\
Group:
  args:
    X: 1
""").generate()
        gitadd = GitAddSpy()
        gen = make_generator(fs, yaml=None)
        with logger.mock_capture_writer() as capture:
            gen.generate(gitadd=gitadd)
        assert not capture.fd.any_contains('Write file')
        assert gitadd.files == []

    def test_missing_model_file_without_groups(self, fs):
        """No model file is created when there is no class to generate."""
        gen = make_generator(fs, yaml="Info:\n")
        gen.generate()
        assert not gen.model_file.exists()

    def test_stale_model_file_is_removed(self, fs):
        """The model of a nav whose groups are all removed is deleted."""
        gen = make_generator(fs, yaml="Info:\n", model="class Removed:\n    pass\n")
        gitadd = GitAddSpy()
        with logger.mock_capture_writer() as capture:
            gen.generate(gitadd=gitadd)
        assert capture.concat() == f"""\
INFO | Write file {gen.tasks_file}
INFO | Delete file {gen.model_file}
INFO | Write file {gen.i18n_file}
"""
        assert gitadd.files == [
            str(gen.tasks_file),
            str(gen.model_file),
            str(gen.i18n_file),
        ]
        assert not gen.model_file.exists()

    def test_absent_stale_model_file_is_not_reported(self, fs):
        """A nav without groups and without a previous model deletes nothing."""
        gen = make_generator(fs, yaml="Info:\n")
        with logger.mock_capture_writer() as capture:
            gen.generate()
        assert capture.concat() == f"""\
INFO | Write file {gen.tasks_file}
INFO | Write file {gen.i18n_file}
"""


class TestGeneratedCode:
    def test_all_arg_kinds_together(self, fs):
        """A nav using every arg kind compiles, imports and validates the input."""
        gen = make_generator(fs, yaml="""\
Base:
  args:
    Mode:
      value: normal
      option: [normal, hard]
    Count:
      value: 3
      range: 1~10
    Ratio:
      value: 1.5
    Enabled: true
    Start:
      dt: datetime
      value: 2020-01-01 00:00:00
    Free:
      dt: filter
      value: A > B

Child:
  parent: Base
  args:
    Mode:
      value: hard
      option: [normal, hard]
    Table:
      dt: secondary-select
      value: 1-1
      option_dict:
        chapter1: [1-1, 1-2]
    Order:
      dt: filter-order
      value: B > A
      option: [A, B]
    Note:
      dt: textarea
      value: |
        line1
        line2
""")
        code = model_code(gen)
        namespace = {}
        exec(compile(code, 'testnav_model.py', 'exec'), namespace)

        base = namespace['Base']
        assert base.get_option('Mode') == ('normal', 'hard')
        # the Child.Mode literal is shared with Base.Mode
        assert 'LITERAL_Base_Mode' in code
        assert code.count('LITERAL_Base_Mode') == 3
        assert 'LITERAL_Child_Mode' not in code

        child = namespace['Child']
        assert child.get_option('Mode') == ('normal', 'hard')
        # the tuple annotation references the shared literal variable
        assert child.__annotations__['Order'] == t.Tuple[namespace['LITERAL_Child_Order'], ...]

        obj = child()
        assert obj.Mode == 'hard'
        assert obj.Count == 3
        assert obj.Ratio == 1.5
        assert obj.Enabled is True
        assert obj.Start == DEFAULT_TIME
        assert obj.Free == ('A', 'B')
        assert obj.Table == '1-1'
        assert obj.Order == ('B', 'A')
        assert obj.Note == 'line1\nline2\n'

        obj = msgspec.convert({'Mode': 'normal', 'Count': 10, 'Order': ['A']}, child)
        assert obj.Mode == 'normal'
        assert obj.Count == 10
        assert obj.Order == ('A',)

        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({'Count': 100}, child)
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({'Mode': 'invalid'}, child)
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert({'Order': ['A', 'C']}, child)
