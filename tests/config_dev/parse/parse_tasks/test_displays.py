"""
Tests of DisplayCard.from_card(), DisplayCard.from_refs() and CardNameBuilder.

A card is a list of items: a group name of the task itself, or a CardItem. A
CardItem with `info` names the card info and must be the first item, a
CardItem with `group` references a group of the task itself or of another
task. The raw info is the explicit info, or the first group that is not a
Scheduler group, so a card without an info still has a stable name.

TaskDefinition has already checked the types and rejected unknown fields, so
these tests only cover the meaning of the items.
"""

import pytest

from alasio.config_dev.parse.base import DefinitionError
from alasio.config_dev.parse.parse_tasks import CardItem, CardNameBuilder, DisplayCard, TaskGroup


def make_card(raw_info, task='Task'):
    """
    Build a card with no groups, for CardNameBuilder tests.

    Args:
        raw_info (str): Raw info of the card
        task (str): Task name of the card

    Returns:
        DisplayCard: Card
    """
    return DisplayCard(task=task, raw_info=raw_info, groups={})


def make_ref(group, task='Task'):
    """
    Build a group reference, for DisplayCard.from_refs() tests.

    Args:
        group (str): Group name
        task (str): Task name of the group

    Returns:
        TaskGroup: Group reference
    """
    return TaskGroup(task=task, group=group, model='')


class TestFromCardGroups:
    def test_one_group(self):
        card = DisplayCard.from_card('Task', ['Group'])
        assert card.raw_info == 'Group'
        assert card.groups == {'Group': make_ref('Group')}

    def test_several_groups(self):
        card = DisplayCard.from_card('Task', ['Group1', 'Group2'])
        assert card.raw_info == 'Group1'
        assert list(card.groups) == ['Group1', 'Group2']

    def test_raw_info_skips_scheduler_groups(self):
        card = DisplayCard.from_card('Task', ['Scheduler', 'Group'])
        assert card.raw_info == 'Group'
        assert list(card.groups) == ['Scheduler', 'Group']

    def test_raw_info_skips_a_scheduler_variant(self):
        card = DisplayCard.from_card('Task', ['SchedulerUedit', 'Group'])
        assert card.raw_info == 'Group'

    def test_raw_info_falls_back_to_the_first_scheduler(self):
        card = DisplayCard.from_card('Task', ['Scheduler', 'SchedulerUedit'])
        assert card.raw_info == 'Scheduler'
        assert list(card.groups) == ['Scheduler', 'SchedulerUedit']

    def test_any_group_starting_with_scheduler_is_skipped(self):
        card = DisplayCard.from_card('Task', ['SchedulerGroup', 'Group'])
        assert card.raw_info == 'Group'

    def test_duplicate_group(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', ['Group', 'Group'])
        assert e.value.msg == 'Duplicate group to display: "Group"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == make_ref('Group')

    def test_empty_card(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [])
        assert e.value.msg == 'Display card must have at least one group or an info'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == []

    def test_invalid_group(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', ['group'])
        assert e.value.msg == 'Group name format invalid: "group"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == 'group'


class TestFromCardRefs:
    def test_cross_task_group(self):
        card = DisplayCard.from_card('Task', [CardItem(group='Group', task='Other')])
        assert card.raw_info == 'Group'
        assert card.groups == {'Group': make_ref('Group', task='Other')}

    def test_group_of_the_task(self):
        card = DisplayCard.from_card('Task', [CardItem(group='Group')])
        assert card.groups == {'Group': make_ref('Group')}

    def test_info_ref(self):
        card = DisplayCard.from_card('Task', [CardItem(info='Fleet')])
        assert card.raw_info == 'Fleet'
        assert card.groups == {}

    def test_info_ref_with_groups(self):
        card = DisplayCard.from_card('Task', [CardItem(info='Fleet'), 'Scheduler', 'Group'])
        assert card.raw_info == 'Fleet'
        assert list(card.groups) == ['Scheduler', 'Group']

    def test_info_ref_with_cross_task_group(self):
        card = DisplayCard.from_card('Task', [
            CardItem(info='Commission'),
            CardItem(group='Preset', task='Commission'),
        ])
        assert card.raw_info == 'Commission'
        assert card.groups['Preset'].task == 'Commission'

    def test_info_ref_must_be_the_first_item(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', ['Group', CardItem(info='Fleet')])
        assert e.value.msg == 'Display info ref must be the first item of a card'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == CardItem(info='Fleet')

    def test_one_info_ref_per_card(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [CardItem(info='Fleet'), CardItem(info='Other')])
        assert e.value.msg == 'Display info ref must be the first item of a card'
        assert e.value.value == CardItem(info='Other')

    def test_info_ref_must_not_have_group(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [CardItem(info='Fleet', group='Group')])
        assert e.value.msg == 'Display info ref must not have "group" or "task"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == CardItem(info='Fleet', group='Group')

    def test_info_ref_must_not_have_task(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [CardItem(info='Fleet', task='Other')])
        assert e.value.msg == 'Display info ref must not have "group" or "task"'
        assert e.value.value == CardItem(info='Fleet', task='Other')

    @pytest.mark.parametrize('info', ['fleet', 'Fleet_1', '1Fleet', 'Fleet-1'], ids=[
        'lower', 'underscore', 'digit-first', 'dash',
    ])
    def test_invalid_info(self, info):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [CardItem(info=info)])
        assert e.value.msg == f'Display info format invalid: "{info}"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == info

    def test_empty_ref(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [CardItem()])
        assert e.value.msg == 'Display ref must have "info" or "group"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == CardItem()

    def test_empty_ref_after_info(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [CardItem(info='Fleet'), CardItem()])
        assert e.value.msg == 'Display ref must have "info" or "group"'
        assert e.value.value == CardItem()

    def test_invalid_group(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [CardItem(group='group')])
        assert e.value.msg == 'Group name format invalid: "group"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == 'group'

    def test_invalid_task(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [CardItem(group='Group', task='other')])
        assert e.value.msg == 'Task name format invalid: "other"'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == 'other'

    def test_duplicate_group_from_different_tasks(self):
        """
        Cards are keyed by group name, so the same group name from two tasks
        can not be displayed in one card.
        """
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_card('Task', [
                CardItem(group='Group', task='Other'),
                CardItem(group='Group', task='Another'),
            ])
        assert e.value.msg == 'Duplicate group to display: "Group"'
        assert e.value.value == make_ref('Group', task='Another')


class TestFromRefs:
    def test_empty(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_refs('Task', '', [], 'flat', ['Task', 'displays'])
        assert e.value.msg == 'Display card must have at least one group or an info'
        assert e.value.keys == ['Task', 'displays']
        assert e.value.value == 'flat'

    def test_info_without_groups(self):
        card = DisplayCard.from_refs('Task', 'Fleet', [], None, ['Task', 'displays'])
        assert card.raw_info == 'Fleet'
        assert card.groups == {}

    def test_raw_info_from_the_first_group(self):
        card = DisplayCard.from_refs(
            'Task', '', [make_ref('Group1'), make_ref('Group2')], None, ['Task', 'displays'])
        assert card.raw_info == 'Group1'
        assert list(card.groups) == ['Group1', 'Group2']

    def test_raw_info_skips_scheduler(self):
        card = DisplayCard.from_refs(
            'Task', '', [make_ref('Scheduler'), make_ref('Group')], None, ['Task', 'displays'])
        assert card.raw_info == 'Group'

    def test_explicit_info_wins(self):
        card = DisplayCard.from_refs(
            'Task', 'Fleet', [make_ref('Group')], None, ['Task', 'displays'])
        assert card.raw_info == 'Fleet'

    def test_duplicate_group(self):
        with pytest.raises(DefinitionError) as e:
            DisplayCard.from_refs(
                'Task', '', [make_ref('Group'), make_ref('Group')], None, ['Task', 'displays'])
        assert e.value.msg == 'Duplicate group to display: "Group"'
        assert e.value.value == make_ref('Group')


class TestCardNameBuilder:
    def test_unique_names(self):
        builder = CardNameBuilder()
        assert builder.add(make_card('Group1')) == 'card-Task-Group1'
        assert builder.add(make_card('Group2')) == 'card-Task-Group2'
        assert list(builder.cards) == ['card-Task-Group1', 'card-Task-Group2']

    def test_duplicate_names_get_an_index(self):
        builder = CardNameBuilder()
        assert builder.add(make_card('Group')) == 'card-Task-Group'
        assert builder.add(make_card('Group')) == 'card-Task-Group2'
        assert builder.add(make_card('Group')) == 'card-Task-Group3'
        assert list(builder.cards) == ['card-Task-Group', 'card-Task-Group2', 'card-Task-Group3']

    def test_different_tasks_do_not_collide(self):
        builder = CardNameBuilder()
        assert builder.add(make_card('Group', task='Task1')) == 'card-Task1-Group'
        assert builder.add(make_card('Group', task='Task2')) == 'card-Task2-Group'

    def test_suffix_can_look_like_another_name(self):
        """
        Names stay unique, but a generated suffix and an authored raw info
        share one namespace: card-Task-G2 is taken by a duplicate, so the
        authored G2 becomes card-Task-G22.
        """
        cards = CardNameBuilder.from_cards([
            make_card('Group'),
            make_card('Group'),
            make_card('Group2'),
        ])
        assert list(cards) == ['card-Task-Group', 'card-Task-Group2', 'card-Task-Group22']

    def test_from_cards_keeps_order(self):
        cards = CardNameBuilder.from_cards([make_card('Group1'), make_card('Group2')])
        assert list(cards) == ['card-Task-Group1', 'card-Task-Group2']

    def test_from_empty_cards(self):
        assert CardNameBuilder.from_cards([]) == {}
