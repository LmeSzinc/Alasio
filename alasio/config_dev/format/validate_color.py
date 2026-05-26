import re

from alasio.config_dev.parse.base import DefinitionError

# Matches #RGB, #RGBA, #RRGGBB, #RRGGBBAA
RE_DASHBOARD_COLOR = re.compile(
    r'^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{4}|[A-Fa-f0-9]{6}|[A-Fa-f0-9]{8})$'
)


def validate_dashboard_color(color, group=None):
    """
    Validate a dashboard color string.

    Args:
        color (str): Color string to validate
        group (str | None): Group name for error context

    Raises:
        DefinitionError: If color is not a valid #RGB/#RGBA/#RRGGBB/#RRGGBBAA string
    """
    if not color:
        return
    if not RE_DASHBOARD_COLOR.match(color):
        keys = ['dashboard_color']
        if group:
            keys = [group, 'dashboard_color']
        raise DefinitionError(
            f'Invalid dashboard_color, expects #RGB #RGBA #RRGGBB #RRGGBBAA',
            keys=keys, value=color
        )
