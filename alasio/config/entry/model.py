from typing import Any, Dict, List, Tuple

from msgspec import Struct
from msgspec.json import Decoder

from alasio.ext.cache import cached_property
from alasio.ext.singleton import Singleton


class ModelConfigRef(Struct):
    """
    {"task": list[str], "group": list[tuple[str, str]]}
    indicates to read task and taskgroups in user config
    """
    task: List[str]
    group: List[Tuple[str, str]]


class ModelNavRef(Struct):
    """
    {
        "file": str,  # indicates to read {nav}_config.json
        "i18n": list[str],  # indicates to read {nav}_i18n.json
        "config": {"task": list[str], "group": list[tuple[str, str]]}
            # indicates to read task and taskgroups in user config
    }
    """
    file: str
    i18n: List[str]
    config: ModelConfigRef


# key: {nav_name}
MODEL_CONFIG_INDEX = Dict[str, ModelNavRef]


class ModelGroupRef(Struct):
    """
    {'file': file, 'cls': class_name, 'task': ref_task_name}
    which indicates:
    - read config from task={ref_task_name} and group={group_name}
    - validate with model file={file}, class {class_name}
    class_name can be:
    - {group_name} for normal group
    - {task_name}_{group_name} that inherits from class {group_name}, for override task group
    """
    file: str
    cls: str
    task: str


class ModelTaskRef(Struct):
    """
    {"group": list[dict], "config": dict}
    """
    group: List[ModelGroupRef]
    config: ModelConfigRef


# key: {task_name}
MODEL_TASK_INDEX = Dict[str, ModelTaskRef]

# {nav}_config.json:
#   {card_name}.{arg_name}.{attr}
# {nav}_i18n.json:
#   {group_name}.{arg_name}.{lang}
# {nav}.index.json
#   {component}.{field}.{lang}
MODEL_DICT_DEPTH3_ANY = Dict[str, Dict[str, Dict[str, Any]]]


class DecoderCache(metaclass=Singleton):
    @cached_property
    def MODEL_CONFIG_INDEX(self):
        return Decoder(MODEL_CONFIG_INDEX)

    @cached_property
    def MODEL_TASK_INDEX(self):
        return Decoder(MODEL_TASK_INDEX)

    @cached_property
    def MODEL_DICT_DEPTH3_ANY(self):
        return Decoder(MODEL_DICT_DEPTH3_ANY)


DECODER_CACHE = DecoderCache()
