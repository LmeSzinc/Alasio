class RequestHumanTakeover(Exception):
    # Request human takeover
    # Alas is unable to handle such error, probably because of wrong settings.
    pass


class TaskEnd(Exception):
    """
    Internal exception that raises on task end and captured by scheduler
    """
    pass
