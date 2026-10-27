"""
The interpreter the wheels are selected for.

Ported from `pip/_internal/models/target_python.py` (pip, MIT): the class and
its tag cache are the source implementation, minus the `format_given()` of the
source (it is a message helper of the pip command line) and the `ios` /
`android` handling (not ported, see the `_tags` module). `normalize_version_info`
is ported from `pip/_internal/utils/misc.py` (pip, MIT).

Usage:
    from alasio.deploy.pip_download._target_python import TargetPython

    target = TargetPython()                  # the running interpreter
    target = TargetPython(py_version_info=(3, 8, 10))
    tags = target.get_sorted_tags()          # most preferred first
"""

import sys

from alasio.deploy.pip_download._compatibility_tags import get_supported, version_info_to_nodot

__all__ = ['TargetPython', 'normalize_version_info']


def normalize_version_info(py_version_info):
    """
    Pad a version info of one to three items into a 3-tuple of ints.

    Ported from pip/_internal/utils/misc.py.

    Args:
        py_version_info (tuple[int, ...]): Version info, e.g. (3, 8)

    Returns:
        tuple[int, int, int]: The version info, padded with zeros

    Raises:
        ValueError: If the tuple is empty or has more than three items
    """
    if len(py_version_info) < 1:
        raise ValueError('py_version_info must be a non-empty tuple of ints')
    if len(py_version_info) > 3:
        raise ValueError('py_version_info may not contain more than 3 elements')
    return tuple(py_version_info) + (0,) * (3 - len(py_version_info))


class TargetPython:
    """
    The properties of the interpreter the wheels are selected for.

    Ported from pip/_internal/models/target_python.py. The default values are
    the running interpreter and the running system; the arguments exist for
    the callers that select wheels for another interpreter (a test, a future
    cross platform fetch).

    Attributes:
        abis (list[str] | None): ABIs of the target, None for the running ones
        implementation (str | None): Implementation of the target, e.g. 'cp',
            None for the running one
        platforms (list[str] | None): Platforms of the target, None for the
            running system
        py_version (str): Version of the target, e.g. '3.8'
        py_version_info (tuple[int, int, int]): Version info of the target,
            e.g. (3, 8, 10)
    """

    __slots__ = [
        '_given_py_version_info', '_valid_tags', '_valid_tags_set',
        'abis', 'implementation', 'platforms', 'py_version', 'py_version_info',
    ]

    def __init__(self, platforms=None, py_version_info=None, abis=None, implementation=None):
        """
        Args:
            platforms (list[str] | None): Platforms the target allows.
                Defaults to None, the platforms of the running system
            py_version_info (tuple[int, ...] | None): Version info of the
                target, one to three ints. Defaults to None, sys.version_info[:3]
            abis (list[str] | None): ABIs of the target. Defaults to None, the
                ABIs of the running interpreter
            implementation (str | None): Implementation of the target, e.g.
                'cp'. Defaults to None, the running interpreter
        """
        # the given version info is kept: the tag generation of the source
        # treats no version differently from the version of the running one
        self._given_py_version_info = py_version_info

        if py_version_info is None:
            py_version_info = sys.version_info[:3]
        else:
            py_version_info = normalize_version_info(py_version_info)

        py_version = '.'.join(map(str, py_version_info[:2]))

        self.abis = abis
        self.implementation = implementation
        self.platforms = platforms
        self.py_version = py_version
        self.py_version_info = py_version_info

        # the cache of get_sorted_tags() / get_unsorted_tags()
        self._valid_tags = None
        self._valid_tags_set = None

    def get_sorted_tags(self):
        """
        The supported tags of the target, most preferred first.

        Ported from pip/_internal/models/target_python.py.

        Returns:
            list[Tag]: The supported tags
        """
        if self._valid_tags is None:
            # no version info given: the tag generation uses its own default
            # (the running interpreter)
            py_version_info = self._given_py_version_info
            if py_version_info is None:
                version = None
            else:
                version = version_info_to_nodot(py_version_info)

            tags = get_supported(
                version=version,
                platforms=self.platforms,
                abis=self.abis,
                impl=self.implementation,
            )
            self._valid_tags = tags

        return self._valid_tags

    def get_unsorted_tags(self):
        """
        The supported tags of the target as a set.

        Ported from pip/_internal/models/target_python.py: the set form is
        what the tag check of a wheel uses, the order of the list is only
        needed for the preference of the wheels.

        Returns:
            set[Tag]: The supported tags
        """
        if self._valid_tags_set is None:
            self._valid_tags_set = set(self.get_sorted_tags())

        return self._valid_tags_set
