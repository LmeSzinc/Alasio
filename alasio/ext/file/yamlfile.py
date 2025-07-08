import yaml

from ..path.atomic import atomic_read_bytes, atomic_write


# https://stackoverflow.com/questions/8640959/how-can-i-control-what-scalar-form-pyyaml-uses-for-my-data/15423007
def str_presenter(dumper, data):
    if len(data.splitlines()) > 1:  # check for multiline string
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


yaml.add_representer(str, str_presenter)
yaml.representer.SafeRepresenter.add_representer(str, str_presenter)


def yaml_loads(data):
    """
    Args:
        data (bytes):

    Returns:
        Any:
    """
    # Use the C extension if available
    Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    return yaml.load(data, Loader)


def yaml_dumps(obj):
    """
    Args:
        obj (Any):

    Returns:
        bytes:
    """
    # Use the C extension if available
    Dumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)
    if not isinstance(obj, list):
        obj = [obj]
    return yaml.dump_all(
        obj, Dumper=Dumper, default_flow_style=False, encoding='utf-8', allow_unicode=True, sort_keys=False)


def read_yaml(file, default_factory=dict):
    """
    Read yaml from file, return default when having error

    Args:
        file (str):
        default_factory (callable):

    Returns:
        Any:
    """
    try:
        data = atomic_read_bytes(file)
    except FileNotFoundError:
        return default_factory()
    try:
        return yaml_loads(data)
    except yaml.YAMLError:
        return default_factory()


def write_yaml(file, obj, skip_same=False):
    """
    Encode obj to yaml and write into file

    Args:
        file (str):
        obj (Any):
        skip_same (bool):
            True to skip writing if existing content is the same as content to write.
            This would reduce disk write but add disk read

    Returns:
        bool: if write
    """
    data = yaml_dumps(obj)
    if skip_same:
        try:
            old = atomic_read_bytes(file)
        except FileNotFoundError:
            old = object()
        if data == old:
            return False
        else:
            atomic_write(file, data)
            return True
    else:
        atomic_write(file, data)
        return True
