import logging

# A default logger
logger = logging.getLogger('Alasio')
logger.setLevel(logging.INFO)
console_formatter = logging.Formatter(
    fmt='%(asctime)s.%(msecs)03d | %(levelname)s │ %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console = logging.StreamHandler()
console.setFormatter(console_formatter)
logger.addHandler(console)


def set_logger(custom_logger):
    """
    Replace default logger

    Args:
        custom_logger:
    """
    global logger
    logger = custom_logger
