from typing import Dict

from msgspec import Struct


class GroupRefModel(Struct):
    file: str
    cls: str


# <task_name>:
#     <group_name>:
#         GroupModelRef
TaskRefModel = Dict[str, Dict[str, GroupRefModel]]
