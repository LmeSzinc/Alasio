class DataInconsistent(Exception):
    """
    Raised when config definition does not match the known format.

    Example:
        - task.index.json indicates to read a class, but class is missing
        - Validation model cannot be default constructed
    """
    pass


class Const:
    """
    Define const of project
    DEV tools may match these values
    """
    # note that dict.fromkeys([...]) is the thing both ordered, unique and fast lookup

    # filepath to the assets folder
    ASSETS_PATH = 'assets'
    ASSETS_MODULE = 'tasks'
    # valid file extension of assets
    # we usually use png because jpg is a lossy compression
    ASSETS_EXT = dict.fromkeys(['.png', '.gif'])
    # valid asset attributes, usually to be static
    # ASSETS_ATTR must not in ASSETS_EXT
    ASSETS_ATTR = dict.fromkeys(['area', 'color', 'button', 'search'])
    # valid lang (or server) of assets
    # ASSETS_LANG must not in ASSETS_EXT, must not in ASSETS_ATTR, nust not be a digit
    ASSETS_LANG = dict.fromkeys(['cn', 'en', 'jp', 'tw'])
    # valid resolution of assets
    ASSETS_RESOLUTION = (1280, 720)
    # Whether to have assets with TEMPLATE_ prefix
    # resolution check will be skipped on templates
    ASSETS_TEMPLATE_PREFIX = True
    # default pad to calculate assets.search
    # negative value to search a larger area
    ASSETS_SEARCH_OUTSET = 20

    GUI_LANGUAGE = ['zh-CN', 'zh-TW', 'en-US', 'ja-JP']
