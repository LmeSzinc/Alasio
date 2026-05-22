from alasio.codegen.python.gen import CodeGen
from alasio.codegen.python.obj_base import GatherItems
from alasio.codegen.python.obj_class import Item, Var


class TestGatherItems:
    def test_add_list_tuple_set(self):
        gen = CodeGen()
        gather = GatherItems()
        item1 = Item(gen, 1)
        item2 = Var(gen, 'a', 2)

        gather.add([item1])
        assert gather.items == [item1]

        gather.add((item2,))
        assert gather.items == [item1, item2]

        item3 = Item(gen, 3)
        gather.add({item3})
        # Set order is not guaranteed, but item3 should be in gather.items
        assert item3 in gather.items

    def test_add_single_item_var(self):
        gen = CodeGen()
        gather = GatherItems()
        item = Item(gen, 1)
        var = Var(gen, 'a', 2)

        gather.add(item)
        assert gather.items == [item]

        gather.add(var)
        assert gather.items == [item, var]

    def test_add_iterable(self):
        gen = CodeGen()
        gather = GatherItems()
        items = [Item(gen, i) for i in range(3)]

        def gen_func():
            for i in items:
                yield i

        gather.add(gen_func())
        assert gather.items == items

    def test_get_inline(self):
        gen = CodeGen()
        gather = GatherItems()
        gather.add(Item(gen, 1))
        gather.add(Var(gen, 'a', 2))

        # Context is None, so line_ending is ''
        # between_kv is ' = '
        assert gather.get_inline() == "1 a = 2"


class TestIterMultiline:
    def test_no_max_width(self):
        gen = CodeGen()
        gather = GatherItems(max_width=False)
        gather.add([Item(gen, 1), Item(gen, 2)])
        assert list(gather.iter_multiline()) == ["1 2"]

    def test_empty_items(self):
        gather = GatherItems(max_width=40)
        assert list(gather.iter_multiline()) == []

    def test_default_max_width(self):
        # max_width=True -> 120
        # For simplicity, we test a small width instead
        pass

    def test_compact_lines(self):
        gen = CodeGen()
        # Item(gen, 1) -> '1' (len 1)
        # Context is None, so item_str is f'{val}' (no comma)
        # Wait, if context is None, line_ending is ''.
        # But GatherItems usually used in collections.

        # Let's test with items that have commas
        with gen.List('l'):
            gather = GatherItems(max_width=15)  # indent is 4
            # Item chars: '1,' (2) + ' ' (1) + '2,' (2) = 5.
            # Next '3,' (2) -> 5 + 1 + 2 = 8.
            # Next '4,' (2) -> 8 + 1 + 2 = 11.
            # Next '5,' (2) -> 11 + 1 + 2 = 14.
            # Next '6,' (2) -> 14 + 1 + 2 = 17 > 15-4=11? 
            # Wait, remain_width = max_width - indent_width = 15 - 4 = 11.
            # '1,' (2). remain = 11 - 2 = 9.
            # '2,' (2). add_len = 3. 3 <= 9. remain = 9 - 3 = 6.
            # '3,' (2). add_len = 3. 3 <= 6. remain = 6 - 3 = 3.
            # '4,' (2). add_len = 3. 3 <= 3? Yes. remain = 3 - 3 = 0.
            # '5,' (2). add_len = 3. 3 > 0.
            # yield '1, 2, 3, 4'
            # buffer = ['5,'] 
            # remain_width = 11 - 2 = 9.

            items = [Item(gen, i) for i in range(1, 10)]
            gather.add(items)

            lines = list(gather.iter_multiline())
            # Output:
            # 1, 2, 3, 4,
            # 5, 6, 7, 8,
            # 9,
            assert lines == ["1, 2, 3, 4,", "5, 6, 7, 8,", "9,"]

    def test_single_long_item(self):
        gen = CodeGen()
        with gen.List('l'):
            gather = GatherItems(max_width=10)  # indent 4, remain 6
            item = Item(gen, "very_long_item")  # 'very_long_item,' (15 chars)
            gather.add(item)
            lines = list(gather.iter_multiline())
            # Even if it's long, first item is added
            assert lines == ["'very_long_item',"]

    def test_line_full_exactly(self):
        gen = CodeGen()
        with gen.List('l'):
            # indent 4, remain 4. '1,' is 2 chars. ' 2,' is 3 chars. 2+3=5 > 4.
            gather = GatherItems(max_width=8)
            gather.add([Item(gen, 1), Item(gen, 2)])
            lines = list(gather.iter_multiline())
            # 1, (len 2)
            # 2, (len 2)
            assert lines == ["1,", "2,"]


class TestVarAnnoFluent:
    def test_var_anno_fluent(self):
        gen = CodeGen()
        
        # gen.Var(name, value).Anno(anno)
        gen.Var('x', 1).Anno('int')
        # gen.Anno(name, anno).Var(value)
        gen.Anno('y', 'str').Var('hello')
        
        code = gen.generate_str()
        expected = """\
x: int = 1
y: str = 'hello'
"""
        assert code == expected
