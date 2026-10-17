"""
The exceptions the map module raises.
"""
from alasio.base.exception import RequestHumanTakeover


class HardNotSatisfied(RequestHumanTakeover):
    pass
