import re

from alasio.config_dev.parse.base import DefinitionError

# Matches #RGB, #RGBA, #RRGGBB, #RRGGBBAA
RE_DASHBOARD_COLOR = re.compile(
    r'^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{4}|[A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$'
)


def validate_dashboard_color(color, group=None):
    """
    Validate a dashboard color string.

    The color is checked on the raw yaml value rather than on the converted
    GroupData, so a wrong type is reported at the same location as a wrong
    format instead of a msgspec error pointing at the whole group.

    Args:
        color (str): Color string to validate, empty to keep the default color
        group (str): Group name for error context, empty to omit it

    Raises:
        DefinitionError: If color is not a str, or is not a valid
            #RGB/#RGBA/#RRGGBB/#RRGGBBAA string
    """
    keys = [group, 'dashboard_color'] if group else ['dashboard_color']
    if not isinstance(color, str):
        raise DefinitionError(
            f'Invalid dashboard_color, expects a str, got {type(color).__name__}',
            keys=keys, value=color
        )
    if not color:
        return
    if not RE_DASHBOARD_COLOR.match(color):
        raise DefinitionError(
            f'Invalid dashboard_color, expects #RGB #RGBA #RRGGBB #RRGGBBAA',
            keys=keys, value=color
        )
