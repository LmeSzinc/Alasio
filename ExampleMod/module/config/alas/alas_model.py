import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class Game(a.GroupBase):
    PackageName: t.Literal['auto'] = 'auto'
    ServerName: t.Literal[
        'disabled',
        'cn_android-0', 'cn_android-1', 'cn_android-2', 'cn_android-3', 'cn_android-4', 'cn_android-5', 'cn_android-6',
        'cn_android-7', 'cn_android-8', 'cn_android-9', 'cn_android-10', 'cn_android-11', 'cn_android-12',
        'cn_android-13', 'cn_android-14', 'cn_android-15', 'cn_android-16', 'cn_android-17', 'cn_android-18',
        'cn_android-19', 'cn_android-20', 'cn_android-21', 'cn_android-22', 'cn_android-23', 'cn_android-24',
        'cn_android-25',
        'cn_ios-0', 'cn_ios-1', 'cn_ios-2', 'cn_ios-3', 'cn_ios-4', 'cn_ios-5', 'cn_ios-6', 'cn_ios-7', 'cn_ios-8',
        'cn_ios-9', 'cn_ios-10',
        'cn_channel-0', 'cn_channel-1', 'cn_channel-2', 'cn_channel-3', 'cn_channel-4',
        'en-0', 'en-1', 'en-2', 'en-3', 'en-4', 'en-5',
        'jp-0', 'jp-1', 'jp-2', 'jp-3', 'jp-4', 'jp-5', 'jp-6', 'jp-7', 'jp-8', 'jp-9', 'jp-10', 'jp-11', 'jp-12',
        'jp-13', 'jp-14', 'jp-15', 'jp-16', 'jp-17',
    ] = 'disabled'
