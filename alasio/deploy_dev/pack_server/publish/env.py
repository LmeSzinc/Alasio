"""
The credentials of a remote, read from the environment of the process.

A config never holds a secret and never names a variable either: the variables
of a remote are its `EnvPrefix` (the default prefix of the driver when it is
empty) plus the field the driver asks for, `{prefix}_{field}`.

There is no credential class and no field table: the driver calls `env_value()`
for the values it needs, and `required=True` at that call site is the whole
description of what it cannot work without. A second table listing the same
fields would only be able to drift from the code.

A credential value is rendered nowhere: the failure message names the variable
that is missing, never a value.
"""

import os

from alasio.deploy_dev.pack_server.publish.common import ConfigError


def env_value(prefix, field, required=False, environ=None):
    """
    The value of one credential variable of a remote.

    An unset variable is the empty string, so a caller compares it with ''
    instead of handling None. `required` is decided at the call site: the
    driver that cannot work without a value asks for it here.

    Args:
        prefix (str): Prefix of the variables of the remote, e.g. 'ALASIO_COS'
        field (str): Name of the field, e.g. 'SECRET_KEY'
        required (bool): True to refuse a variable that is not set. Defaults to
            False
        environ (Mapping): Environment to read. Defaults to None: os.environ

    Returns:
        str: The value, '' when the variable is not set and not required

    Raises:
        ConfigError: If the value is required and is missing or empty
    """
    name = f'{prefix}_{field}'
    value = (os.environ if environ is None else environ).get(name, '')
    if required and not value:
        raise ConfigError(f'Missing the environment variable {name}')
    return value
