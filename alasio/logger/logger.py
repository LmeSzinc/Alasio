import logging

logger = logging.getLogger('Alasio')
logger.setLevel(logging.INFO)
console_formatter = logging.Formatter(
    fmt='%(asctime)s.%(msecs)03d | %(levelname)s │ %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console = logging.StreamHandler()
console.setFormatter(console_formatter)
logger.addHandler(console)
