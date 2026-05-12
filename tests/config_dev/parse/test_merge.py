from msgspec import UNSET

from alasio.config_dev.parse.parse_args import ArgData
from alasio.config_dev.parse.parse_groups import GroupData


class TestMerge:
    def test_arg_data_merge(self):
        arg1 = ArgData(dt='input', value='v1', hide=True)
        arg2 = ArgData(dt='input-int', value=10, option=['a', 'b'])

        # Merge arg1 with arg2
        merged = arg1.merge(arg2)

        assert merged.dt == 'input-int'
        assert merged.value == 10
        assert merged.hide == False  # Default value from arg2 overrides arg1.hide=True
        assert merged.option == ['a', 'b']

        # Test partial merge (if only some fields are set to non-UNSET)
        arg3 = ArgData(dt='input', value='v3')
        # We manually set some fields to UNSET to simulate partial data if needed
        # But ArgData from_arg_data usually populates defaults.
        # Let's test with UNSET directly.
        arg_partial = ArgData(dt='checkbox', value=True, option=UNSET)
        merged2 = arg2.merge(arg_partial)
        assert merged2.dt == 'checkbox'
        assert merged2.value == True
        assert merged2.option == ['a', 'b']  # Kept from arg2 because arg_partial.option is UNSET

    def test_group_data_merge(self):
        arg1 = ArgData(dt='input', value='v1')
        arg2 = ArgData(dt='input-int', value=10)
        # dashboard='value' requires arg 'Value'
        group1 = GroupData(name='G1', dashboard='value', args={'Value': arg1})

        arg3 = ArgData(dt='checkbox', value=True)
        arg1_override = ArgData(dt='input', value='v1-new')
        group2 = GroupData(name='G2', dashboard_color='#FF0000', args={'Value': arg1_override, 'a2': arg3})

        merged = group1.merge(group2)

        assert merged.name == 'G2'

    def test_group_data_merge_replace_attributes(self):
        # Test that simple attributes are replaced even if empty in other
        # dashboard='value' requires arg 'Value'
        arg_v = ArgData(dt='input', value='v')
        group1 = GroupData(name='G1', dashboard='value', dashboard_color='#FF0000', args={'Value': arg_v})
        group2 = GroupData(name='G2', dashboard='', dashboard_color='')

        merged = group1.merge(group2)
        assert merged.name == 'G2'
        assert merged.dashboard == ''  # Replaced even if empty
        assert merged.dashboard_color == ''  # Replaced even if empty

    def test_group_data_merge_fresh_objects(self):
        # Test that merged objects and their nested structures are fresh
        arg1 = ArgData(dt='input', value='v1', option=['o1'])
        group1 = GroupData(name='G1', args={'a1': arg1})

        arg2 = ArgData(dt='input', value='v2', option=['o2'])
        group2 = GroupData(name='G2', args={'a2': arg2})

        merged = group1.merge(group2)

        # Check instance freshness
        assert merged is not group1
        assert merged is not group2
        assert merged.args['a1'] is not arg1
        assert merged.args['a2'] is not arg2

        # Check nested list freshness
        assert merged.args['a1'].option is not arg1.option
        assert merged.args['a1'].option == ['o1']

        # Mutate original and check merged
        arg1.option.append('o_mutated')
        assert 'o_mutated' not in merged.args['a1'].option
