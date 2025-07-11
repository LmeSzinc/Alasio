from typing import Any, Literal, Tuple, Union

from msgspec import Struct, field


class RequestEvent(Struct, omit_defaults=True):
    # Topic, topic name.
    t: str
    # Operation.
    # operation can be omitted, if so, operation is considered to be "sub"
    # if operation is "sub", operation should be omitted
    o: Literal['sub', 'unsub', 'rpc'] = 'sub'
    # Function, RPC function Name.
    # if operation is "sub" or "unsub", "f" should be omitted
    f: str = ''
    # Value, RPC function argument value.
    # if operation is "sub" or "unsub", "v" should be omitted
    # value can be omitted, if so, value is consider to be empty dict {}
    v: Any = field(default_factory=dict)
    # ID, RPC event ID, a random unique ID to track RPC calls.
    # if operation is "sub" or "unsub", "i" should be omitted
    # A ResponseEvent with the same ID will be sent when the RPC event is finished
    i: str = ''


class ResponseEvent(Struct, omit_defaults=True):
    # Topic.
    t: str
    # Operation.
    # operation may be omitted, if so, operation is "add"
    o: Literal['full', 'add', 'set', 'del'] = 'add'
    # Keys.
    # keys may be omitted, if so, keys is (), meaning doing operation at data root
    k: Tuple[Union[str, int], ...] = ()
    # Value.
    # value may be omitted, if so, value is None
    # if operation is "del", value will be omitted
    v: Any = None
    # ID, RPC event ID, a random unique ID to track RPC calls.
    # If present, this event is a response to an RPC call.
    # RPC event ID only comes with topic and value.
    # - If event success, value is omitted.
    # - If event failed, value is a string of error message.
    i: str = ''


class AccessDenied(Exception):
    """
    Internal error that raises when RequestEvent is not allowed
    """
    pass
