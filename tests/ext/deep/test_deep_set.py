"""
Tests for the write functions in ``alasio.ext.deep``:

- ``deep_set``: set a value into a nested dict, creating missing levels
- ``deep_default``: set a value only when the key does not exist
- ``dict_update``: safely update a dict

``deep_set``/``deep_default`` correct non-dict intermediate levels on the fly
(e.g. an int at an intermediate key is replaced by a dict), so callers should
always use the return value:

    d = deep_set(d, keys, value)
"""

from alasio.ext.deep import deep_default, deep_set, dict_update


class TestDeepSet:
    def test_deep_set_basic(self):
        d = {'a': {'b': 1}}
        deep_set(d, 'a.b', 2)
        assert d == {'a': {'b': 2}}

    def test_deep_set_create(self):
        d = {}
        assert deep_set(d, 'a.b.c', 1) == {'a': {'b': {'c': 1}}}
        assert d == {'a': {'b': {'c': 1}}}

    def test_deep_set_list_keys(self):
        d = {}
        assert deep_set(d, ['a', 'b'], 1) == {'a': {'b': 1}}

    def test_deep_set_overwrite(self):
        d = {'a': 1}
        assert deep_set(d, 'a', 2) == {'a': 2}

    def test_deep_set_overwrite_path_multi(self):
        # Overwriting a deeper path where the intermediate is not a dict
        d = {'a': {'b': 1}}
        deep_set(d, 'a.b.c', 2)
        assert d == {'a': {'b': {'c': 2}}}

    def test_deep_set_correction_non_dict(self):
        # input d=1
        # a=2
        assert deep_set(1, 'a', 2) == {'a': 2}
        # a.b=2
        assert deep_set(1, 'a.b', 2) == {'a': {'b': 2}}
        # a.b.c=2
        assert deep_set(1, 'a.b.c', 2) == {'a': {'b': {'c': 2}}}
        # []=2
        # If keys is empty, implementation returns {} for non-dict input
        assert deep_set(1, [], 2) == {}

    def test_deep_set_correction_existing_dict(self):
        # input {a: 1}
        d = {'a': 1}
        # a=2
        assert deep_set(d.copy(), 'a', 2) == {'a': 2}
        # a.b=2
        # 'a' was 1 (int), should be corrected to dict
        assert deep_set(d.copy(), 'a.b', 2) == {'a': {'b': 2}}
        # a.b.c=2
        assert deep_set(d.copy(), 'a.b.c', 2) == {'a': {'b': {'c': 2}}}
        # []=2
        # keys=[] returns raw_d if raw_d is dict
        assert deep_set(d.copy(), [], 2) == {'a': 1}

    def test_deep_set_non_iterable_keys(self):
        # `keys` is not iterable, treated as empty keys
        d = {'a': 1}
        assert deep_set(d, 123, 2) == {'a': 1}
        assert deep_set(1, 123, 2) == {}

    def test_deep_set_none_value(self):
        d = {}
        assert deep_set(d, 'a.b', None) == {'a': {'b': None}}

    def test_deep_set_list_middle_corrected(self):
        # Write ops assume dict only: a list met on the key path is treated as
        # a dict and replaced. This is the expected behaviour, not an error.
        d = {'a': [1]}
        deep_set(d, 'a.0.b', 2)
        assert d == {'a': {'0': {'b': 2}}}

    def test_deep_set_unhashable_key(self):
        # A non-hashable key cannot be a dict key: return raw_d unchanged
        d = {'a': 1}
        assert deep_set(d, [['x']], 2) == {'a': 1}
        assert deep_set(d, [['x'], 'y'], 2) == {'a': 1}
        # Non-dict raw_d cannot be corrected either, return {}
        assert deep_set(1, [['x']], 2) == {}

    def test_deep_set_empty_keys_non_dict_containers(self):
        # keys=[] on a non-dict container returns {} (contract: returns dict)
        assert deep_set([1, 2], [], 3) == {}
        assert deep_set({1, 2}, [], 3) == {}
        assert deep_set('abc', [], 3) == {}

    def test_deep_set_returns_same_object(self):
        # When the root is a dict and keys is non-empty, the same object is returned
        d = {'a': {}}
        assert deep_set(d, 'a.b', 1) is d


class TestDeepDefault:
    def test_deep_default_existing(self):
        d = {'a': {'b': 1}}
        deep_default(d, 'a.b', 2)
        assert d == {'a': {'b': 1}}

    def test_deep_default_missing(self):
        d = {'a': {'b': 1}}
        deep_default(d, 'a.c', 2)
        assert d == {'a': {'b': 1, 'c': 2}}

    def test_deep_default_create(self):
        d = {}
        assert deep_default(d, 'a.b', 1) == {'a': {'b': 1}}

    def test_deep_default_correction_non_dict(self):
        # input d=1
        assert deep_default(1, 'a', 2) == {'a': 2}
        assert deep_default(1, 'a.b', 2) == {'a': {'b': 2}}
        assert deep_default(1, 'a.b.c', 2) == {'a': {'b': {'c': 2}}}
        assert deep_default(1, [], 2) == {}

    def test_deep_default_correction_existing_dict(self):
        # input {a: 1}
        d = {'a': 1}
        # a=2, key exists so value unchanged
        assert deep_default(d.copy(), 'a', 2) == {'a': 1}
        # a.b=2, 'a' was 1 (int), corrected to dict
        assert deep_default(d.copy(), 'a.b', 2) == {'a': {'b': 2}}
        # a.b.c=2
        assert deep_default(d.copy(), 'a.b.c', 2) == {'a': {'b': {'c': 2}}}
        # []=2
        assert deep_default(d.copy(), [], 2) == {'a': 1}

    def test_deep_default_none_value(self):
        # None counts as existing, so default does not overwrite it
        d = {'a': None}
        deep_default(d, 'a', 1)
        assert d == {'a': None}

    def test_deep_default_unhashable_key(self):
        # A non-hashable key cannot be a dict key: return raw_d unchanged
        d = {'a': 1}
        assert deep_default(d, [['x']], 2) == {'a': 1}
        assert deep_default(d, [['x'], 'y'], 2) == {'a': 1}
        # Non-dict raw_d cannot be corrected either, return {}
        assert deep_default(1, [['x']], 2) == {}

    def test_deep_default_empty_keys_non_dict_containers(self):
        # keys=[] on a non-dict container returns {} (contract: returns dict)
        assert deep_default([1, 2], [], 3) == {}
        assert deep_default({1, 2}, [], 3) == {}
        assert deep_default('abc', [], 3) == {}


class TestDictUpdate:
    def test_dict_update_basic(self):
        d = {'a': 1}
        assert dict_update(d, {'b': 2}) == {'a': 1, 'b': 2}

    def test_dict_update_invalid(self):
        assert dict_update(None, {'a': 1}) == {'a': 1}
        assert dict_update({'a': 1}, None) == {'a': 1}

    def test_dict_update_aggressive(self):
        # Update with self
        d = {'a': 1}
        assert dict_update(d, d) == d
        # Update with incompatible type
        assert dict_update({'a': 1}, 123) == {'a': 1}

    def test_dict_update_non_dict_d(self):
        # `d` is not a dict (AttributeError) -> return new
        assert dict_update(1, {'a': 1}) == {'a': 1}
        assert dict_update(None, None) is None

    def test_dict_update_new_not_dict(self):
        # `new` is not a dict (TypeError) -> return d
        assert dict_update({'a': 1}, [1, 2]) == {'a': 1}

    def test_dict_update_mutates_in_place(self):
        d = {'a': 1}
        ret = dict_update(d, {'b': 2})
        assert ret is d
