"""
Host-level webapp language/theme preferences, received through the stdin
contract.

The webapp main process (Electron AppState) is the single source of truth
for the host language and theme. It sends ``command:set_lang:{lang}`` and
``command:set_theme:{theme}`` lines through the supervisor stdin pipe; the
supervisor forwards them verbatim and this module persists them into
``config/deploy.yaml`` (``Webapp.Lang`` / ``Webapp.Theme``) idempotently:
when the value already matches the current config nothing is written
(zero IO).

The stdin input is trusted (there is no round trip), validation here is
defensive only: invalid values are logged and ignored.

This is a host-level contract, distinct from the per-connection ws
``set_lang`` RPC (alasio.backend.topic.state), which only updates the
language of one websocket connection.
"""

from alasio.config.const import Const
from alasio.deploy.config.model import DeployConfig
from alasio.logger import logger

SUPPORTED_SET_LANG = set(Const.GUI_LANGUAGE) | {'system'}
SUPPORTED_SET_THEME = {'system', 'light', 'dark'}
# Dpi scaling arrives as the string form of a bool ('true'/'false')
SUPPORTED_SET_DPI_SCALING = {'true', 'false'}


def handle_stdin_set_lang(lang):
    """
    Persist the host-level webapp language into deploy.yaml.

    Args:
        lang (str): 'system' or one of Const.GUI_LANGUAGE

    Returns:
        bool: True if the yaml was written, False if the value was invalid,
            already persisted or the write failed
    """
    if lang not in SUPPORTED_SET_LANG:
        logger.warning(f'Invalid set_lang value from stdin: {lang!r}')
        return False
    deploy = DeployConfig()
    if deploy.config.data.Webapp.Lang == lang:
        # Idempotent: value already persisted, skip the write (zero IO)
        return False
    if deploy.config.set(('Webapp', 'Lang'), lang):
        return deploy.config.write()
    return False


def handle_stdin_set_theme(theme):
    """
    Persist the host-level webapp theme into deploy.yaml.

    Args:
        theme (str): 'system', 'light' or 'dark'

    Returns:
        bool: True if the yaml was written, False if the value was invalid,
            already persisted or the write failed
    """
    if theme not in SUPPORTED_SET_THEME:
        logger.warning(f'Invalid set_theme value from stdin: {theme!r}')
        return False
    deploy = DeployConfig()
    if deploy.config.data.Webapp.Theme == theme:
        # Idempotent: value already persisted, skip the write (zero IO)
        return False
    if deploy.config.set(('Webapp', 'Theme'), theme):
        return deploy.config.write()
    return False


def handle_stdin_set_dpi_scaling(value):
    """
    Persist the host-level webapp dpi scaling into deploy.yaml.

    Dpi scaling is a single value (true = follow the system DPI scaling,
    false = force scale factor 1), unlike language/theme there is no
    config/display split. It only affects the electron window through its
    startup command line switch, so a change applies on the next launch.

    Args:
        value (str): 'true' or 'false' (string form over the stdin contract)

    Returns:
        bool: True if the yaml was written, False if the value was invalid,
            already persisted or the write failed
    """
    if value not in SUPPORTED_SET_DPI_SCALING:
        logger.warning(f'Invalid set_dpi_scaling value from stdin: {value!r}')
        return False
    dpi_scaling = value == 'true'
    deploy = DeployConfig()
    if deploy.config.data.Webapp.DpiScaling == dpi_scaling:
        # Idempotent: value already persisted, skip the write (zero IO)
        return False
    if deploy.config.set(('Webapp', 'DpiScaling'), dpi_scaling):
        return deploy.config.write()
    return False
