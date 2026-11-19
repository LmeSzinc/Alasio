"""
Tests for the comment map and the comment writer of
:mod:`alasio.ext.file.yamlconfig`.

The comments are placed in two steps: :func:`build_help_map` reads the model
and builds a flat map of every node it declares, then :func:`iter_yaml_rows`
reads the dumped text and :func:`insert_comments_iter` writes the comment of a
path above its line. The model says what a node is, the text only says where
its line is.
"""

from typing import Dict, List, Optional, Tuple, Union

import msgspec
from msgspec import Meta, Struct
from typing_extensions import Annotated

from alasio.ext.file.yamlconfig import (
    ANY_KEY, YamlConfig, build_help_map, insert_comments, insert_comments_iter, iter_yaml_rows, resolve_help
)
from alasio.ext.file.yamlfile import yaml_dumps, yaml_loads
from alasio.testing.filesystem import fs  # noqa: F401


def help_of(text):
    """
    The msgspec annotation that gives a field its help text.

    Args:
        text (str | list[str]): Help text

    Returns:
        msgspec.Meta:
    """
    return Meta(extra={'help': text})


def render(data):
    """
    Dump the data into yaml and insert the comments of its model.

    Args:
        data: A model instance

    Returns:
        str: The commented yaml text
    """
    return insert_comments(yaml_dumps(msgspec.to_builtins(data)).decode('utf-8'), build_help_map(type(data)))


class Item(Struct):
    """Element of a list of structs, used in tests."""

    Name: Annotated[str, help_of('item name')] = ''
    Desc: Annotated[str, help_of('item description')] = ''


class Remote(Struct):
    """Value of a map the operator keys, used in tests."""

    Bucket: Annotated[str, help_of('bucket name')] = ''


class Inner(Struct):
    """Nested struct, used in tests."""

    port: Annotated[int, help_of('inner port')] = 0


class Cos(Struct, tag='cos', tag_field='Driver'):
    """Variant of a tagged union, used in tests."""

    Bucket: Annotated[str, help_of('cos bucket')] = ''


class WebDav(Struct, tag='webdav', tag_field='Driver'):
    """Variant of a tagged union, used in tests."""

    BaseUrl: Annotated[str, help_of('webdav url')] = ''


class SequenceModel(Struct):
    """A model whose fields are the shapes a config holds, used in tests."""

    Items: Annotated[List[Item], help_of('the items')] = msgspec.field(default_factory=list)
    Names: Annotated[List[str], help_of('the names')] = msgspec.field(default_factory=list)
    Publish: Annotated[Dict[str, Remote], help_of('the remotes')] = msgspec.field(default_factory=dict)


class TreeNode(Struct):
    """A model that holds one of itself, used in tests.

    The class is declared at the module level: msgspec resolves the forward
    reference of the annotation against the module of the class.
    """

    label: Annotated[str, help_of('the label')] = ''
    child: Optional['TreeNode'] = None


class TestBuildHelpMap:
    """The flat help map of the model."""

    def test_flat(self):
        class Model(Struct):
            port: Annotated[int, help_of('port help')] = 0
            name: Annotated[str, help_of('name help')] = ''

        assert build_help_map(Model) == {
            ('port',): 'port help',
            ('name',): 'name help',
        }

    def test_a_key_without_help(self):
        """A node the model declares is in the map, with None for its help."""
        class Model(Struct):
            port: int = 0

        assert build_help_map(Model) == {('port',): None}

    def test_multiline_help(self):
        class Model(Struct):
            port: Annotated[int, help_of('line 1\nline 2')] = 0
            name: Annotated[str, help_of(['a', 'b'])] = ''

        assert build_help_map(Model) == {
            ('port',): ['line 1', 'line 2'],
            ('name',): ['a', 'b'],
        }

    def test_a_field_of_a_base_class(self):
        """A field of a base class is a field of the struct."""

        class Base(Struct):
            port: Annotated[int, help_of('base port')] = 0

        class Child(Base):
            name: Annotated[str, help_of('child name')] = ''

        assert build_help_map(Child) == {('port',): 'base port', ('name',): 'child name'}

    def test_a_renamed_field(self):
        """The file holds the encoded name of a field, the help is its own."""

        class Model(Struct):
            port: Annotated[int, help_of('port help')] = 0
            name: Annotated[str, help_of('name help')] = msgspec.field(default='', name='Name')

        assert build_help_map(Model) == {('port',): 'port help', ('Name',): 'name help'}

    def test_an_instance_documents_its_class(self):
        assert build_help_map(Item(Name='a')) == build_help_map(Item)

    def test_nested(self):
        class Model(Struct):
            inner: Annotated[Inner, help_of('the inner')] = msgspec.field(default_factory=Inner)

        assert build_help_map(Model) == {
            ('inner',): 'the inner',
            ('inner', 'port'): 'inner port',
        }

    def test_the_shape_of_a_config(self):
        """A map the operator keys, a list of structs and a list of scalars."""
        assert build_help_map(SequenceModel) == {
            ('Items',): 'the items',
            ('Items', ANY_KEY): None,
            ('Items', ANY_KEY, 'Name'): 'item name',
            ('Items', ANY_KEY, 'Desc'): 'item description',
            ('Names',): 'the names',
            ('Names', ANY_KEY): None,
            ('Publish',): 'the remotes',
            ('Publish', ANY_KEY): None,
            ('Publish', ANY_KEY, 'Bucket'): 'bucket name',
        }

    def test_a_sequence_of_a_sequence(self):
        class Model(Struct):
            nested: Annotated[List[List[str]], help_of('the nested lists')] = \
                msgspec.field(default_factory=list)

        assert build_help_map(Model) == {
            ('nested',): 'the nested lists',
            ('nested', ANY_KEY): None,
            ('nested', ANY_KEY, ANY_KEY): None,
        }

    def test_union_variants_share_the_level(self):
        """Every variant of a union writes the same level, their fields are one set."""

        class Model(Struct):
            Remotes: Annotated[List[Union[Cos, WebDav]], help_of('the remotes')] = \
                msgspec.field(default_factory=list)

        assert build_help_map(Model) == {
            ('Remotes',): 'the remotes',
            ('Remotes', ANY_KEY): None,
            ('Remotes', ANY_KEY, 'Driver'): 'Driver selects the variant of this entry',
            ('Remotes', ANY_KEY, 'Bucket'): 'cos bucket',
            ('Remotes', ANY_KEY, 'BaseUrl'): 'webdav url',
        }

    def test_a_map_of_a_map(self):
        """Each level the operator keys adds its own ANY_KEY segment."""

        class Model(Struct):
            nested: Annotated[Dict[str, Dict[str, Inner]], help_of('the nested maps')] = \
                msgspec.field(default_factory=dict)

        assert build_help_map(Model) == {
            ('nested',): 'the nested maps',
            ('nested', ANY_KEY): None,
            ('nested', ANY_KEY, ANY_KEY): None,
            ('nested', ANY_KEY, ANY_KEY, 'port'): 'inner port',
        }

    def test_a_tagged_struct_of_its_own(self):
        """The tag is a key msgspec writes, the model declares no field for it."""

        class Model(Struct):
            remote: Cos = msgspec.field(default_factory=Cos)

        assert build_help_map(Model) == {
            ('remote',): None,
            ('remote', 'Driver'): 'Driver selects the variant of this entry',
            ('remote', 'Bucket'): 'cos bucket',
        }

    def test_a_struct_dumped_as_a_sequence(self):
        """Such a struct writes its values without keys, there is nothing above."""

        class Positional(Struct, array_like=True):
            port: Annotated[int, help_of('port help')] = 0

        class Model(Struct):
            positional: Positional = msgspec.field(default_factory=Positional)

        assert build_help_map(Model) == {('positional',): None}

    def test_a_model_that_holds_itself(self):
        """The walk ends at the class it already went through, instead of looping."""
        assert build_help_map(TreeNode) == {
            ('label',): 'the label',
            ('child',): None,
        }

    def test_the_map_does_not_follow_the_data(self):
        """A list of a thousand elements documents its element type once."""
        many = SequenceModel(Items=[Item(Name=str(i)) for i in range(1000)])
        assert build_help_map(many) == build_help_map(SequenceModel())

    def test_omit_defaults_fields_are_documented(self):
        """The map describes the model: a field the dump may skip still has one."""

        class Model(Struct, omit_defaults=True):
            kept: Annotated[str, help_of('kept help')] = 'k'
            default: Annotated[str, help_of('default help')] = 'd'

        assert build_help_map(Model) == {
            ('kept',): 'kept help',
            ('default',): 'default help',
        }


class TestIterYamlRows:
    """The key path of every line of a yaml text."""

    def test_basic(self):
        rows = list(iter_yaml_rows("""\
port: 8080
name: server
"""))
        assert rows == [
            (('port',), 'port: 8080'),
            (('name',), 'name: server'),
        ]

    def test_nested(self):
        rows = list(iter_yaml_rows("""\
outer:
  port: 8080
"""))
        assert rows == [
            (('outer',), 'outer:'),
            (('outer', 'port'), '  port: 8080'),
        ]

    def test_same_key_different_levels(self):
        rows = list(iter_yaml_rows("""\
port: 1
inner:
  port: 2
"""))
        assert rows == [
            (('port',), 'port: 1'),
            (('inner',), 'inner:'),
            (('inner', 'port'), '  port: 2'),
        ]

    def test_block_scalar_content(self):
        rows = list(iter_yaml_rows("""\
desc: |-
  line1
  line2
port: 8080
"""))
        assert rows == [
            (('desc',), 'desc: |-'),
            (None, '  line1'),
            (None, '  line2'),
            (('port',), 'port: 8080'),
        ]

    def test_block_scalar_with_an_indent_indicator(self):
        rows = list(iter_yaml_rows("""\
desc: |2-
    a: b
port: 8080
"""))
        assert rows == [
            (('desc',), 'desc: |2-'),
            (None, '    a: b'),
            (('port',), 'port: 8080'),
        ]

    def test_comment_and_empty_line(self):
        rows = list(iter_yaml_rows("""\
# comment

port: 8080
"""))
        assert rows == [
            (None, '# comment'),
            (None, ''),
            (('port',), 'port: 8080'),
        ]

    def test_sequence_of_mappings(self):
        rows = list(iter_yaml_rows("""\
items:
- name: a
  port: 8080
- name: b
"""))
        assert rows == [
            (('items',), 'items:'),
            (('items', ANY_KEY, 'name'), '- name: a'),
            (('items', ANY_KEY, 'port'), '  port: 8080'),
            (('items', ANY_KEY, 'name'), '- name: b'),
        ]

    def test_sequence_of_scalars(self):
        rows = list(iter_yaml_rows("""\
names:
- master
- dev
port: 8080
"""))
        assert rows == [
            (('names',), 'names:'),
            (None, '- master'),
            (None, '- dev'),
            (('port',), 'port: 8080'),
        ]

    def test_sequence_indented_under_its_key(self):
        rows = list(iter_yaml_rows("""\
items:
  - name: a
    port: 8080
"""))
        assert rows == [
            (('items',), 'items:'),
            (('items', ANY_KEY, 'name'), '  - name: a'),
            (('items', ANY_KEY, 'port'), '    port: 8080'),
        ]

    def test_sequence_inside_an_element(self):
        rows = list(iter_yaml_rows("""\
items:
- name: a
  tags:
  - x
  - y
- name: b
"""))
        assert rows == [
            (('items',), 'items:'),
            (('items', ANY_KEY, 'name'), '- name: a'),
            (('items', ANY_KEY, 'tags'), '  tags:'),
            (None, '  - x'),
            (None, '  - y'),
            (('items', ANY_KEY, 'name'), '- name: b'),
        ]

    def test_nested_sequence(self):
        rows = list(iter_yaml_rows("""\
items:
- - name: a
  - name: b
"""))
        assert rows == [
            (('items',), 'items:'),
            (('items', ANY_KEY, ANY_KEY, 'name'), '- - name: a'),
            (('items', ANY_KEY, ANY_KEY, 'name'), '  - name: b'),
        ]

    def test_block_scalar_element(self):
        """The content of a block scalar element is text, not keys."""
        rows = list(iter_yaml_rows("""\
items:
- |-
  a: b
  c: d
port: 8080
"""))
        assert rows == [
            (('items',), 'items:'),
            (None, '- |-'),
            (None, '  a: b'),
            (None, '  c: d'),
            (('port',), 'port: 8080'),
        ]

    def test_lone_dash(self):
        rows = list(iter_yaml_rows("""\
items:
-
  name: a
port: 8080
"""))
        assert rows == [
            (('items',), 'items:'),
            (None, '-'),
            (('items', ANY_KEY, 'name'), '  name: a'),
            (('port',), 'port: 8080'),
        ]

    def test_scalar_with_a_colon_is_not_a_key(self):
        """A url in a sequence is a scalar: the colon does not end a key name."""
        rows = list(iter_yaml_rows("""\
urls:
- https://example.com/pack
"""))
        assert rows == [
            (('urls',), 'urls:'),
            (None, '- https://example.com/pack'),
        ]


class TestResolveHelp:
    """The help of a key path, with the unnamed levels of the map."""

    def test_named_path(self):
        help_map = {('Repo',): None, ('Repo', 'Remote'): 'remote url'}
        assert resolve_help(help_map, ('Repo', 'Remote')) == 'remote url'

    def test_a_node_without_help(self):
        help_map = {('Repo',): None}
        assert resolve_help(help_map, ('Repo',)) is None

    def test_a_key_of_a_map(self):
        """The key is not in the map, its parent's unnamed level is."""
        help_map = {
            ('Publish',): 'the remotes',
            ('Publish', ANY_KEY): None,
            ('Publish', ANY_KEY, 'EnvPrefix'): 'env prefix',
        }
        assert resolve_help(help_map, ('Publish', 'myname', 'EnvPrefix')) == 'env prefix'

    def test_sequence_element(self):
        help_map = {
            ('Items',): 'the items',
            ('Items', ANY_KEY): None,
            ('Items', ANY_KEY, 'Name'): 'item name',
        }
        assert resolve_help(help_map, ('Items', ANY_KEY, 'Name')) == 'item name'

    def test_an_exact_key_wins_over_the_unnamed_level(self):
        help_map = {('Inner',): 'inner', ('Inner', ANY_KEY): 'unnamed'}
        assert resolve_help(help_map, ('Inner',)) == 'inner'

    def test_the_whole_path_is_found_first(self):
        """A path the map holds is read at once, its ancestors are not needed."""
        help_map = {('A', 'B', 'C'): 'deep help'}
        assert resolve_help(help_map, ('A', 'B', 'C')) == 'deep help'

    def test_the_step_walk_is_the_fallback(self):
        """A path the map does not hold is walked, the unnamed level included."""
        help_map = {
            ('Publish',): 'the remotes',
            ('Publish', ANY_KEY): None,
            ('Publish', ANY_KEY, 'EnvPrefix'): 'env prefix',
        }
        assert resolve_help(help_map, ('Publish', 'myname', 'EnvPrefix')) == 'env prefix'

    def test_unknown_path(self):
        help_map = {('Repo',): None, ('Repo', 'Remote'): 'remote url'}
        assert resolve_help(help_map, ('Repo', 'Author')) is None
        assert resolve_help(help_map, ('Other',)) is None

    def test_a_path_below_a_leaf(self):
        help_map = {('Repo', 'Remote'): 'remote url'}
        assert resolve_help(help_map, ('Repo', 'Remote', 'deeper')) is None

    def test_a_part_of_the_path_that_is_not_defined(self):
        """A key neither named nor below an unnamed level has no help."""
        help_map = {('Repo', 'Remote'): 'remote url'}
        assert resolve_help(help_map, ('Repo', 'Typo', 'Deeper')) is None


class TestInsertCommentsIter:
    """The comment lines written before the rows of a text."""

    def test_comment_before_key(self):
        rows = iter_yaml_rows('port: 8080\n')
        out = list(insert_comments_iter(rows, {('port',): 'port help'}))
        assert out == ['# port help', 'port: 8080']

    def test_multiline_help(self):
        rows = iter_yaml_rows('port: 8080\n')
        out = list(insert_comments_iter(rows, {('port',): ['line 1', 'line 2']}))
        assert out == ['# line 1', '# line 2', 'port: 8080']

    def test_no_help(self):
        rows = iter_yaml_rows('port: 8080\n')
        assert list(insert_comments_iter(rows, {})) == ['port: 8080']

    def test_a_key_without_help(self):
        """The key is in the map with no help, it gets no comment."""
        rows = iter_yaml_rows('port: 8080\n')
        assert list(insert_comments_iter(rows, {('port',): None})) == ['port: 8080']

    def test_indent_preserved(self):
        rows = iter_yaml_rows('outer:\n  port: 8080\n')
        out = list(insert_comments_iter(rows, {('outer',): None, ('outer', 'port'): 'port help'}))
        assert out == ['outer:', '  # port help', '  port: 8080']

    def test_unnamed_level(self):
        help_map = {
            ('Publish',): 'the remotes',
            ('Publish', ANY_KEY): None,
            ('Publish', ANY_KEY, 'Bucket'): 'bucket name',
        }
        rows = iter_yaml_rows("""\
Publish:
  cos-main:
    Bucket: main
  cos-backup:
    Bucket: backup
""")
        out = list(insert_comments_iter(rows, help_map))
        assert out == [
            '# the remotes',
            'Publish:',
            '  cos-main:',
            '    # bucket name',
            '    Bucket: main',
            '  cos-backup:',
            '    # bucket name',
            '    Bucket: backup',
        ]


class TestInsertComments:
    """The commented text of a whole document."""

    def test_flat(self):
        class Model(Struct):
            port: Annotated[int, help_of('port help')] = 8080
            name: Annotated[str, help_of('name help')] = 'server'

        assert render(Model()) == """\
# port help
port: 8080
# name help
name: server
"""

    def test_multiline_help(self):
        class Model(Struct):
            port: Annotated[int, help_of(['line 1', 'line 2'])] = 8080

        assert render(Model()) == """\
# line 1
# line 2
port: 8080
"""

    def test_nested(self):
        class Model(Struct):
            name: Annotated[str, help_of('server name')] = 'server'
            inner: Annotated[Inner, help_of('the inner')] = msgspec.field(default_factory=Inner)

        assert render(Model()) == """\
# server name
name: server
# the inner
inner:
  # inner port
  port: 0
"""

    def test_map_the_operator_keys(self):
        data = SequenceModel(Publish={'cos-main': Remote(Bucket='b')})
        assert render(data) == """\
# the items
Items: []
# the names
Names: []
# the remotes
Publish:
  cos-main:
    # bucket name
    Bucket: b
"""

    def test_sequence_of_structs(self):
        data = SequenceModel(Items=[Item(Name='a', Desc='line1\nline2'), Item(Name='b')])
        assert render(data) == """\
# the items
Items:
# item name
- Name: a
  # item description
  Desc: |-
    line1
    line2
# item name
- Name: b
  # item description
  Desc: ''
# the names
Names: []
# the remotes
Publish: {}
"""

    def test_sequence_of_scalars(self):
        """A scalar element has no node of its own, only the field is commented."""
        assert render(SequenceModel(Names=['master', 'dev'])) == """\
# the items
Items: []
# the names
Names:
- master
- dev
# the remotes
Publish: {}
"""

    def test_sequence_of_sequences(self):
        """A nested sequence starts on the line of the dash of its element."""

        class Model(Struct):
            nested: Annotated[List[List[str]], help_of('the nested lists')] = \
                msgspec.field(default_factory=list)

        assert render(Model(nested=[['p', 'q'], ['r']])) == """\
# the nested lists
nested:
- - p
  - q
- - r
"""

    def test_struct_as_the_first_key_of_an_element(self):
        """The first key of an element shares the line of its dash, a nested struct follows."""

        class Wrap(Struct):
            # the field is named in lower case: a class body binds the name of an
            # assigned field before it evaluates the annotation of that field
            inner: Annotated[Inner, help_of('the inner')] = msgspec.field(default_factory=Inner)

        class Model(Struct):
            Wraps: Annotated[List[Wrap], help_of('the wraps')] = msgspec.field(default_factory=list)

        assert render(Model(Wraps=[Wrap()])) == """\
# the wraps
Wraps:
# the inner
- inner:
    # inner port
    port: 0
"""

    def test_tagged_union(self):
        """The tag msgspec writes is the first key of the mapping, and is commented."""

        class Model(Struct):
            Remote: Union[Cos, WebDav] = msgspec.field(default_factory=Cos)

        assert render(Model(Remote=WebDav(BaseUrl='https://x'))) == """\
Remote:
  # Driver selects the variant of this entry
  Driver: webdav
  # webdav url
  BaseUrl: https://x
"""

    def test_block_scalar_content_is_not_a_key(self):
        """The content of a block scalar is text, no comment goes inside it."""

        class Model(Struct):
            desc: Annotated[str, help_of('the description')] = ''
            port: Annotated[int, help_of('port help')] = 8080

        assert render(Model(desc='a: b\nc: d')) == """\
# the description
desc: |-
  a: b
  c: d
# port help
port: 8080
"""

    def test_empty_containers(self):
        class Model(Struct):
            mapping: Annotated[Dict[str, int], help_of('the mapping')] = msgspec.field(default_factory=dict)
            items: Annotated[List[int], help_of('the items')] = msgspec.field(default_factory=list)

        assert render(Model()) == """\
# the mapping
mapping: {}
# the items
items: []
"""

    def test_no_comment(self):
        class Plain(Struct):
            port: int = 0

        class Model(Struct):
            port: int = 8080
            inner: Plain = msgspec.field(default_factory=Plain)

        text = yaml_dumps(msgspec.to_builtins(Model())).decode('utf-8')
        assert insert_comments(text, build_help_map(Model())) == text

    def test_empty_text(self):
        assert insert_comments('', {}) == ''

    def test_the_comments_do_not_change_the_data(self):
        data = SequenceModel(
            Items=[Item(Name='a', Desc='line1\nline2'), Item(Name='b')],
            Names=['master', 'dev'],
            Publish={'cos-main': Remote(Bucket='b'), 'nas': Remote(Bucket='c')},
        )
        assert msgspec.convert(yaml_loads(render(data)), SequenceModel) == data

    def test_an_int_key_of_a_map(self):
        """A map keyed by another type is a key of the file as well."""

        class Model(Struct):
            ports: Annotated[Dict[int, str], help_of('the ports')] = msgspec.field(default_factory=dict)

        assert render(Model(ports={1: 'a', 2: 'b'})) == """\
# the ports
ports:
  1: a
  2: b
"""

    def test_a_tuple_field_value_is_a_sequence(self):
        class Model(Struct):
            names: Annotated[Tuple[str, ...], help_of('the names')] = ()

        assert render(Model(names=('a', 'b'))) == """\
# the names
names:
- a
- b
"""


class TestYamlConfigWrite:
    """The comments reach the file through YamlConfig."""

    def test_write_comments(self, fs):
        class Model(Struct):
            port: Annotated[int, help_of('port help')] = 8080
            items: Annotated[List[Item], help_of('the items')] = msgspec.field(default_factory=list)

        config = YamlConfig('/config.yaml', Model)
        config.data.items = [Item(Name='a')]
        assert config.write() is True
        with open('/config.yaml', encoding='utf-8') as f:
            assert f.read() == """\
# port help
port: 8080
# the items
items:
# item name
- Name: a
  # item description
  Desc: ''
"""

    def test_write_is_the_same_twice(self, fs):
        """The commented text is stable, the second write finds it up to date."""
        config = YamlConfig('/config.yaml', SequenceModel)
        config.data.Items = [Item(Name='a')]
        config.data.Publish = {'cos-main': Remote(Bucket='b')}
        assert config.write() is True
        assert config.write() is False
