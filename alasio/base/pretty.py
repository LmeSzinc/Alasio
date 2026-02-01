def dict2kv(dic, drop_none=False):
    """
    Format dict to readable string

    Args:
        dic (dict): {'path': 'Scheduler.ServerUpdate', 'value': True}
        drop_none: True to drop None values

    Returns:
        str: "path='Scheduler.ServerUpdate', value=True"
    """
    if drop_none:
        items = [f'{k}={repr(v)}' for k, v in dic.items() if v is not None]
    else:
        items = [f'{k}={repr(v)}' for k, v in dic.items()]
    return ', '.join(items)
