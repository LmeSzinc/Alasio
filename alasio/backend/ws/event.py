from typing import Any, Literal, Tuple, Union

import msgspec


class RequestEvent(msgspec.Struct, omit_defaults=True):
    # topic.
    t: str
    # operation.
    # operation can be omitted, if so, operation is considered to be "sub"
    # if operation is "sub", operation should be omitted
    o: Literal['sub', 'unsub', 'add', 'set', 'del'] = 'sub'
    # keys.
    # keys can be omitted, if so, keys is consider to be (), meaning doing operation at data root
    # if operation is "sub" or "unsub", keys should be omitted
    k: Tuple[Union[str, int]] = ()
    # value.
    # value can be omitted, if so, value is consider to be None
    # if operation is "sub" or "unsub" or "del", value should be omitted
    v: Any = None


class ResponseEvent(msgspec.Struct, omit_defaults=True):
    # topic.
    t: str
    # operation.
    # operation may be omitted, if so, operation is "add"
    o: Literal['full', 'add', 'set', 'del'] = 'add'
    # keys.
    # keys may be omitted, if so, keys is (), meaning doing operation at data root
    k: Tuple[Union[str, int]] = ()
    # value.
    # value may be omitted, if so, value is None
    # if operation is "del", value will be omitted
    v: Any = None
