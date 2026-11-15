"""
The wheel file model: the name, the version and the tags of a wheel file.

Ported from `pip/_internal/models/wheel.py` (pip, MIT): the `Wheel` class and
its tag queries (`support_index_min`, `find_most_preferred_tag`, `supported`)
are the source implementation. The filename parse itself lives in
`packaging/utils.py` of the source, here it is `parse_wheel_filename` of the
sibling `_utils` module.

Usage:
    from alasio.deploy.pip_download._wheel import Wheel, is_pure_wheel

    wheel = Wheel('httpx-0.28.1-py3-none-any.whl')
    wheel.name                      # 'httpx'
    wheel.supported(supported_tags) # True
    is_pure_wheel(wheel.file_tags)  # True
"""

from alasio.deploy.pip_download._utils import InvalidWheelFilename, parse_wheel_filename

__all__ = ['InvalidWheelFilename', 'Wheel', 'is_pure_wheel']


def is_pure_wheel(tags):
    """
    Whether a set of tags describes a pure python wheel of python 3.

    The ABI and the platform tags are `none-any`, so the file installs on any
    system, and the python tag covers python 3 ('py3', 'py2.py3', 'py38', ...).
    This is the rule of the pack channel: the dependency packs are built from
    the one wheel a version ships that matches it, see
    alasio.deploy_dev.pack_server.repo_dep.

    Args:
        tags (Iterable[Tag]): The tags of a wheel, e.g. Wheel.file_tags

    Returns:
        bool: True for a pure python wheel of python 3
    """
    return any(
        tag.abi == 'none' and tag.platform == 'any' and tag.interpreter.startswith('py3')
        for tag in tags
    )


class Wheel:
    """
    One wheel file.

    Ported from pip/_internal/models/wheel.py.

    Attributes:
        filename (str): Filename of the wheel
        name (str): PEP 503 normalized distribution name
        version (str): Normalized version of the file
        build_tag (tuple): () when the file carries no build tag, else
            (build number, rest of the build tag)
        file_tags (frozenset[Tag]): Tags the file declares
    """

    def __init__(self, filename):
        """
        Args:
            filename (str): Filename of a wheel, e.g. 'httpx-0.28.1-py3-none-any.whl'

        Raises:
            InvalidWheelFilename: If the filename is not a wheel filename
        """
        self.filename = filename

        try:
            wheel_info = parse_wheel_filename(filename)
        except InvalidWheelFilename as e:
            raise InvalidWheelFilename(str(e)) from None

        self.name, version, self.build_tag, self.file_tags = wheel_info
        self.version = str(version)

    def get_formatted_file_tags(self):
        """
        Returns:
            list[str]: The tags of the file, sorted, e.g. ['py3-none-any']
        """
        return sorted(str(tag) for tag in self.file_tags)

    def support_index_min(self, tags):
        """
        The lowest index one of the file tags achieves in the supported tags.

        Ported from pip/_internal/models/wheel.py: the supported tags are
        ordered most preferred first, so the lowest index is the best match
        of the file.

        Args:
            tags (Sequence[Tag]): Supported tags, most preferred first

        Returns:
            int: The index of the best matching tag

        Raises:
            ValueError: If none of the file tags matches a supported tag
        """
        try:
            return next(i for i, tag in enumerate(tags) if tag in self.file_tags)
        except StopIteration:
            raise ValueError() from None

    def find_most_preferred_tag(self, tags, tag_to_priority):
        """
        The priority of the most preferred supported tag of the file.

        Ported from pip/_internal/models/wheel.py: the map form of
        `support_index_min`, for the callers that precompute the priority of
        every supported tag instead of scanning the list.

        Args:
            tags (Sequence[Tag]): Supported tags
            tag_to_priority (dict[Tag, int]): Priority of a supported tag,
                lower is more preferred

        Returns:
            int: The lowest priority of the file tags

        Raises:
            ValueError: If none of the file tags matches a supported tag
        """
        return min(
            tag_to_priority[tag] for tag in self.file_tags if tag in tag_to_priority)

    def supported(self, tags):
        """
        Whether the wheel is compatible with one of the given tags.

        Ported from pip/_internal/models/wheel.py.

        Args:
            tags (Iterable[Tag]): Supported tags

        Returns:
            bool: True when one of the file tags is supported
        """
        return not self.file_tags.isdisjoint(tags)
