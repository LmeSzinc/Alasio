import pytest

from alasio.ext.deep import *

COMPLEX_DICT = {
    'a': 1,
    'b': {
        'c': 2,
        'd': {
            'e': 3,
            'f': {'f1': 4}
        },
        'g': 5
    },
    'h': {
        'i': 6
    },
    'j': 7,
    'k': [8, 9]
}


class TestDeepGet:
    def test_deep_get_basic(self):
        d = {'a': {'b': {'c': 1}}}
        assert deep_get(d, 'a.b.c') == 1
        assert deep_get(d, ['a', 'b', 'c']) == 1

    def test_deep_get_default(self):
        d = {'a': {'b': {'c': 1}}}
        assert deep_get(d, 'a.b.d', default=2) == 2
        assert deep_get(d, 'a.x.c', default=None) is None

    def test_deep_get_list(self):
        d = {'a': [{'b': 1}, {'b': 2}]}
        assert deep_get(d, ['a', 0, 'b']) == 1
        assert deep_get(d, ['a', 1, 'b']) == 2
        # Index error
        assert deep_get(d, ['a', 2, 'b'], default='err') == 'err'

    def test_deep_get_list_input(self):
        # Support list as input d
        d = [10, 20, 30]
        assert deep_get(d, [1]) == 20
        assert deep_get(d, [5], default=None) is None


class TestDeepGetWithError:
    def test_deep_get_with_error_basic(self):
        d = {'a': {'b': 1}}
        assert deep_get_with_error(d, 'a.b') == 1

    def test_deep_get_with_error_missing(self):
        d = {'a': {'b': 1}}
        with pytest.raises(KeyError):
            deep_get_with_error(d, 'a.c')
        with pytest.raises(KeyError):
            deep_get_with_error(d, 'x.b')

    def test_deep_get_with_error_list(self):
        d = [1, 2]
        assert deep_get_with_error(d, [0]) == 1
        with pytest.raises(KeyError):
            deep_get_with_error(d, [2])  # IndexError raised and caught as KeyError


class TestDeepExist:
    def test_deep_exist_basic(self):
        d = {'a': {'b': 1}}
        assert deep_exist(d, 'a.b') is True
        assert deep_exist(d, 'a.c') is False
        assert deep_exist(d, 'x.b') is False

    def test_deep_exist_list(self):
        d = [1, {'a': 2}]
        assert deep_exist(d, [0]) is True
        assert deep_exist(d, [1, 'a']) is True
        assert deep_exist(d, [2]) is False


class TestCorrection:
    @pytest.mark.parametrize("func", [deep_set, deep_default])
    def test_correction_non_dict(self, func):
        # input d=1
        # a=2
        assert func(1, 'a', 2) == {'a': 2}
        # a.b=2
        assert func(1, 'a.b', 2) == {'a': {'b': 2}}
        # a.b.c=2
        assert func(1, 'a.b.c', 2) == {'a': {'b': {'c': 2}}}
        # []=2
        # If keys is empty, implementation returns {} for non-dict input
        assert func(1, [], 2) == {}

    @pytest.mark.parametrize("func", [deep_set, deep_default])
    def test_correction_existing_dict(self, func):
        # input {a: 1}
        d = {'a': 1}
        # a=2
        if func == deep_set:
            assert func(d.copy(), 'a', 2) == {'a': 2}
        else:
            assert func(d.copy(), 'a', 2) == {'a': 1}

        # a.b=2
        # 'a' was 1 (int), should be corrected to dict
        assert func(d.copy(), 'a.b', 2) == {'a': {'b': 2}}

        # a.b.c=2
        assert func(d.copy(), 'a.b.c', 2) == {'a': {'b': {'c': 2}}}

        # []=2
        # keys=[] returns raw_d if raw_d is dict
        assert func(d.copy(), [], 2) == {'a': 1}


class TestDeepIterComplex:
    def test_deep_iter_depth1(self):
        res = list(deep_iter(COMPLEX_DICT, depth=1))
        # depth=1 should return top level items
        # yield [k], v
        assert len(res) == 5
        paths = [r[0] for r in res]
        assert ['a'] in paths
        assert ['b'] in paths  # 'b' is dict, but depth=1 iterates it
        assert (['a'], 1) in res

    def test_deep_iter_depth2(self):
        res = list(deep_iter(COMPLEX_DICT, depth=2))
        # b: c, d, g
        # h: i
        # only depth 2 items are yielded
        assert (['b', 'c'], 2) in res
        assert (['b', 'd'], {'e': 3, 'f': {'f1': 4}}) in res
        assert (['b', 'g'], 5) in res
        assert (['h', 'i'], 6) in res
        assert len(res) == 4

    def test_deep_iter_depth2_min1(self):
        res = list(deep_iter(COMPLEX_DICT, min_depth=1, depth=2))
        # a, j, k (depth 1)
        # b.c, b.d, b.g, h.i (depth 2)
        assert (['a'], 1) in res
        assert (['b', 'c'], 2) in res
        assert len(res) == 7

    def test_deep_iter_depth2_3(self):
        # min_depth=2, depth=3
        res = list(deep_iter(COMPLEX_DICT, min_depth=2, depth=3))
        # Depth 2: b.c, b.g, h.i
        # Depth 3: b.d.e, b.d.f
        assert (['b', 'c'], 2) in res
        assert (['b', 'd', 'e'], 3) in res
        # b.d.f is dict, so it's not yielded if current < depth?
        # current=2: b.d is dict, added to q.
        # current=3: b.d.e (yield), b.d.f (yield as it's the target depth)
        assert (['b', 'd', 'f'], {'f1': 4}) in res
        assert len(res) == 5

    def test_deep_iter_complex(self):
        all_items = list(deep_iter(COMPLEX_DICT, min_depth=1, depth=4))

        expected_paths = [
            (['a'], 1),
            (['b', 'c'], 2),
            (['b', 'd', 'e'], 3),
            (['b', 'd', 'f', 'f1'], 4),
            (['b', 'g'], 5),
            (['h', 'i'], 6),
            (['j'], 7),
            (['k'], [8, 9])
        ]

        for path, val in expected_paths:
            assert (path, val) in all_items
        assert len(all_items) == len(expected_paths)


class TestListSupport:
    def test_deep_get_list(self):
        d = [0, [10, 11], 2]
        assert deep_get(d, [1, 0]) == 10
        assert deep_get(d, [1, 1]) == 11
        assert deep_get(d, [2]) == 2

    def test_deep_default_nested(self):
        d = {'a': {'b': 1}}
        deep_default(d, 'a.b', 2)
        assert d == {'a': {'b': 1}}
        deep_default(d, 'a.c', 2)
        assert d == {'a': {'b': 1, 'c': 2}}

    def test_deep_exist_list(self):
        d = [0, {'a': 1}]
        assert deep_exist(d, [0]) is True
        assert deep_exist(d, [1, 'a']) is True
        assert deep_exist(d, [2]) is False

    def test_deep_pop_list(self):
        d = [0, 1, 2]
        assert deep_pop(d, [1]) == 1
        assert d == [0, 2]

        # Nested list pop
        d = {'a': [10, 20]}
        assert deep_pop(d, ['a', 0]) == 10
        assert d == {'a': [20]}


class TestDeepPop:
    def test_deep_pop_basic(self):
        d = {'a': {'b': 1}}
        assert deep_pop(d, 'a.b') == 1
        assert d == {'a': {}}

    def test_deep_pop_missing(self):
        d = {'a': 1}
        assert deep_pop(d, 'b', default='miss') == 'miss'

    def test_deep_pop_list(self):
        d = {'a': [1, 2, 3]}
        assert deep_pop(d, ['a', 1]) == 2
        assert d == {'a': [1, 3]}


class TestDictUpdate:
    def test_dict_update_basic(self):
        d = {'a': 1}
        assert dict_update(d, {'b': 2}) == {'a': 1, 'b': 2}

    def test_dict_update_invalid(self):
        assert dict_update(None, {'a': 1}) == {'a': 1}
        assert dict_update({'a': 1}, None) == {'a': 1}


class TestDeepIterDepth:
    def test_deep_iter_depth1(self):
        d = {'a': 1, 'b': 2}
        assert dict(deep_iter_depth1(d)) == d
        assert list(deep_iter_depth1(None)) == []
        assert list(deep_keys_depth1(d)) == ['a', 'b']
        assert list(deep_values_depth1(d)) == [1, 2]

    def test_deep_iter_depth2(self):
        d = {'a': {'b': 1}, 'c': {'d': 2}}
        assert list(deep_iter_depth2(d)) == [('a', 'b', 1), ('c', 'd', 2)]
        assert list(deep_iter_depth2({'a': 1})) == []
        assert list(deep_keys_depth2(d)) == [('a', 'b'), ('c', 'd')]
        assert list(deep_values_depth2(d)) == [1, 2]

    def test_deep_iter_depth2_inconsistent(self):
        # Inconsistent depth levels
        d = {'a': {'b': 1}, 'c': 2, 'd': {'e': 3}}
        # deep_iter_depth2 should iterate a.b and d.e, skipping c
        res = list(deep_iter_depth2(d))
        assert ('a', 'b', 1) in res
        assert ('d', 'e', 3) in res
        assert len(res) == 2


class TestDeepIterGeneral:
    def test_deep_iter_basic(self):
        d = {'a': {'b': {'c': 1}}}
        # list[key], value
        assert list(deep_iter(d, depth=3)) == [(['a', 'b', 'c'], 1)]
        assert list(deep_keys(d, depth=3)) == [(['a', 'b', 'c'])]
        assert list(deep_values(d, depth=3)) == [1]

    def test_deep_iter_min_depth(self):
        d = {'a': 1, 'b': {'c': 2}}
        res = list(deep_iter(d, min_depth=1, depth=2))
        assert (['a'], 1) in res
        assert (['b', 'c'], 2) in res

    def test_deep_iter_aggressive(self):
        # Empty dict
        assert list(deep_iter({}, depth=3)) == []
        # depth < min_depth raises AssertionError
        with pytest.raises(AssertionError):
            list(deep_iter({}, min_depth=2, depth=1))
        # Non-dict
        assert list(deep_iter(None)) == []


class TestDeepIterDiffAndPatch:
    def test_deep_iter_diff_basic(self):
        d1 = {'a': 1, 'b': {'c': 2}}
        d2 = {'a': 2, 'b': {'c': 2, 'd': 3}}
        diff = list(deep_iter_diff(d1, d2))
        # path, val_before, val_after
        assert (['a'], 1, 2) in diff
        assert (['b', 'd'], None, 3) in diff

    def test_deep_iter_patch_basic(self):
        before = {'a': 1, 'b': 2}
        after = {'a': 1, 'c': 3}
        patch = list(deep_iter_patch(before, after))
        # op, path, val_after
        assert (OP_DEL, ['b'], None) in patch
        assert (OP_ADD, ['c'], 3) in patch

    def test_deep_iter_diff_non_dict(self):
        assert list(deep_iter_diff({'a': 1}, 2)) == [([], {'a': 1}, 2)]
        assert list(deep_iter_diff(1, {'a': 2})) == [([], 1, {'a': 2})]

    def test_deep_iter_patch_non_dict(self):
        assert list(deep_iter_patch({'a': 1}, 2)) == [(OP_SET, [], 2)]

    def test_deep_iter_diff_nested_non_dict(self):
        # Test when nested values have different types
        d1 = {'a': {'b': 1}}
        d2 = {'a': 2}
        diff = list(deep_iter_diff(d1, d2))
        assert (['a'], {'b': 1}, 2) in diff

    def test_deep_aggressive_circular(self):
        # deep_iter should be safe against circular references due to depth limit
        d = {}
        d['a'] = d
        # Should not infinite loop
        res = list(deep_iter(d, depth=5))
        # depth 1: (['a'], d)
        # depth 2: (['a', 'a'], d)
        # ...
        assert len(res) == 1
        assert res[0][0] == ['a', 'a', 'a', 'a', 'a']

    def test_deep_aggressive_types(self):
        # Test deep_get with non-hashable keys in path (though split() makes them strings)
        # If we pass a list of keys containing a dict
        d = {'a': 1}
        assert deep_get(d, [{'a': 1}]) is None  # KeyError or TypeError caught

        # Test deep_set with non-dict d is not supported by implementation
        # and documented as "Can only set dict"

    def test_deep_set_overwrite_path_multi(self):
        # Test overwriting deeper path
        d = {'a': {'b': 1}}
        deep_set(d, 'a.b.c', 2)
        assert d == {'a': {'b': {'c': 2}}}

    def test_deep_get_empty_keys(self):
        d = {'a': 1}
        assert deep_get(d, []) == d
        # ''.split('.') is [''], d[''] raises KeyError, returns default
        assert deep_get(d, '') is None
        assert deep_get(d, '.') is None

    def test_deep_unicode(self):
        d = {'你好': {'世界': 123}}
        assert deep_get(d, '你好.世界') == 123
        assert deep_exist(d, '你好.世界') is True

    def test_deep_pop_empty(self):
        d = {'a': 1}
        assert deep_pop(d, '') is None
        assert deep_pop(d, []) is None  # keys[-1] IndexError caught

    def test_dict_update_aggressive(self):
        # Update with self
        d = {'a': 1}
        assert dict_update(d, d) == d
        # Update with incompatible type
        assert dict_update({'a': 1}, 123) == {'a': 1}

    def test_deep_iter_min_eq_depth(self):
        d = {'a': {'b': 1}}
        assert list(deep_iter(d, min_depth=2, depth=2)) == [(['a', 'b'], 1)]
