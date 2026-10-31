from datetime import datetime
from typing import Any, Dict, List, Tuple, Union

from msgspec import DecodeError, Struct
from msgspec.json import decode as json_decode
from msgspec.msgpack import Decoder as MsgpackDecoder, decode as msgpack_decode, encode as msgpack_encode
from msgspecerror import ErrorInfo

from alasio.ext.cache import cached_property
from alasio.ext.cache.resource import ResourceCache
from alasio.ext.file.msgspecfile import JsonCacheTTL
from alasio.ext.path.atomic import atomic_read_bytes
from alasio.ext.singleton import Singleton
from alasio.logger import logger


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
    {"group": dict[str, dict], "config": dict}
    """
    group: Dict[str, ModelGroupRef]
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
# queue.index.json
#   {task_name}.{lang}
MODEL_DICT_DEPTH2_ANY = Dict[str, Dict[str, Any]]

# model.index.json
#   {nav}_model.py: {dependency file: [class name]}
# The key order of the file is the load order, a dependency before its user.
MODEL_MODEL_INDEX = Dict[str, Dict[str, List[str]]]


class DecoderCache(metaclass=Singleton):
    """
    Decoders of the msgpack packs, see JSON_PACK_CACHE
    """

    @cached_property
    def MODEL_CONFIG_INDEX(self):
        return MsgpackDecoder(MODEL_CONFIG_INDEX)

    @cached_property
    def MODEL_TASK_INDEX(self):
        return MsgpackDecoder(MODEL_TASK_INDEX)

    @cached_property
    def MODEL_DICT_DEPTH2_ANY(self):
        return MsgpackDecoder(MODEL_DICT_DEPTH2_ANY)

    @cached_property
    def MODEL_DICT_DEPTH3_ANY(self):
        return MsgpackDecoder(MODEL_DICT_DEPTH3_ANY)


DECODER_CACHE = DecoderCache()


class ConfigSetEvent(Struct):
    task: str
    group: str
    arg: str
    value: Any
    error: Union[ErrorInfo, Exception, None] = None


class TaskItem(Struct):
    TaskName: str
    NextRun: datetime

    def __str__(self):
        # show as str, to avoid showing a long tzinfo object
        return f'Task(TaskName="{self.TaskName}", NextRun="{self.NextRun}")'

    __repr__ = __str__


class JsonPackCache(ResourceCache):
    """
    Read a json file once and keep the msgpack form of it.

    The pack is 40% smaller than the json source (the repeated keys are
    compressed away), so a mod that is never opened costs its packs only. The
    decoded value is built per use by MOD_JSON_CACHE, which holds it with a
    TTL: decoding a pack costs the same single decode as reading the json.
    """

    def load_resource(self, file: str):
        return msgpack_encode(json_decode(atomic_read_bytes(file)))


class ModJsonCacheTTL(JsonCacheTTL):
    def load_resource(self, file: str, decoder: MsgpackDecoder = None, default_factory=dict):
        try:
            pack = JSON_PACK_CACHE.get(file)
            if decoder is None:
                return msgpack_decode(pack)
            else:
                return decoder.decode(pack)
        except (FileNotFoundError, DecodeError) as e:
            logger.error(f'Failed to read model json: "{file}": {e}')
            return default_factory()
        except Exception as e:
            logger.exception(e)
            return default_factory()


JSON_PACK_CACHE = JsonPackCache()
MOD_JSON_CACHE = ModJsonCacheTTL()
