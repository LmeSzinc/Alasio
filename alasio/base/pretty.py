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


def pretty_time(second):
    """
    Format time with adaptive units (s or ms or us)

    Args:
        time_seconds (float): Time in seconds

    Returns:
        str: Formatted time string
    """
    if second >= 1:
        return f'{second:.3f}s'
    if second >= 0.001:
        return f"{second * 1000:.3f}ms"
    return f"{second * 1000000:.3f}us"
