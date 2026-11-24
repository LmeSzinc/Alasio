import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

LITERAL_GeneralShop_BuyOrder = t.Literal[
    'BookRedT3', 'BookYellowT3', 'BookBlueT3', 'BookRedT2', 'Cube', 'FoodT6', 'FoodT5',
]


class GeneralShop(a.GroupBase):
    UseGems: bool = False
    Refresh: bool = False
    BuySkinBox: bool = False
    ConsumeCoins: bool = False
    BuyOrder: t.Tuple[LITERAL_GeneralShop_BuyOrder, ...] = (
        'BookRedT3', 'BookYellowT3', 'BookBlueT3', 'BookRedT2', 'Cube', 'FoodT6', 'FoodT5',
    )


LITERAL_SchedulerGeneralShop_ServerUpdate = t.Literal['00:00, 12:00, 18:00']


class SchedulerGeneralShop(a.Scheduler):
    ServerUpdate: LITERAL_SchedulerGeneralShop_ServerUpdate = '00:00, 12:00, 18:00'


LITERAL_GuildShop_BuyOrder = t.Literal[
    'PlateT4-general', 'PlateT4-gun', 'PlateT4-torpedo', 'PlateT4-antiair', 'PlateT4-plane', 'PlateT3-general',
    'PlateT3-gun', 'PlateT3-torpedo', 'PlateT3-antiair', 'PlateT3-plane', 'PlateT2-general', 'PlateT2-gun',
    'PlateT2-torpedo', 'PlateT2-antiair', 'PlateT2-plane', 'BookT3-red', 'BookT3-blue', 'BookT3-yellow', 'BookT2-red',
    'BookT2-blue', 'BookT2-yellow', 'PR-neptune', 'PR-monarch', 'PR-ibuki', 'PR-izumo', 'PR-roon', 'PR-saintlouis',
    'PR-seattle', 'PR-georgia', 'PR-kitakaze', 'PR-gascogne', 'PR-cheshire', 'PR-mainz', 'PR-odin', 'PR-champagne',
    'CatT3', 'CatT2', 'Chip', 'Retrofit-dd', 'Retrofit-cl', 'Retrofit-bb', 'Retrofit-cv', 'FoodT6', 'FoodT5',
    'BoxT4-eagle', 'BoxT4-royal', 'BoxT4-sakura', 'BoxT4-ironblood', 'BoxT3-eagle', 'BoxT3-royal', 'BoxT3-sakura',
    'BoxT3-ironblood',
]


class GuildShop(a.GroupBase):
    Refresh: bool = True
    BuyOrder: t.Tuple[LITERAL_GuildShop_BuyOrder, ...] = (
        'PlateT4-gun', 'BookT3-red', 'PR-neptune', 'CatT3', 'Chip', 'BookT2-red', 'Retrofit-cl', 'FoodT6', 'FoodT5',
        'CatT2', 'BoxT4-ironblood',
    )


LITERAL_MedalShop_BuyOrder = t.Literal[
    'DR', 'PR', 'BookT3-red', 'BookT3-yellow', 'BookT3-blue', 'BookT2-red', 'BookT2-yellow', 'BookT2-blue',
    'RetrofitT1-dd', 'RetrofitT1-cl', 'RetrofitT1-bb', 'RetrofitT1-cv', 'RetrofitT2-dd', 'RetrofitT2-cl',
    'RetrofitT2-bb', 'RetrofitT2-cv', 'RetrofitT3-dd', 'RetrofitT3-cl', 'RetrofitT3-bb', 'RetrofitT3-cv', 'FoodT6',
    'FoodT5', 'PlateT1-general', 'PlateT1-gun', 'PlateT1-torpedo', 'PlateT1-antiair', 'PlateT1-plane',
    'PlateT2-general', 'PlateT2-gun', 'PlateT2-torpedo', 'PlateT2-antiair', 'PlateT2-plane', 'PlateT3-general',
    'PlateT3-gun', 'PlateT3-torpedo', 'PlateT3-antiair', 'PlateT3-plane',
]


class MedalShop(a.GroupBase):
    BuyOrder: t.Tuple[LITERAL_MedalShop_BuyOrder, ...] = (
        'DR', 'PR', 'BookT3-red', 'BookT3-yellow', 'BookT3-blue', 'BookT2-red', 'BookT2-yellow', 'BookT2-blue',
        'RetrofitT3-cl', 'FoodT6', 'FoodT5', 'PlateT3-general',
    )


LITERAL_MeritShop_BuyOrder = t.Literal['Cube']


class MeritShop(a.GroupBase):
    Refresh: bool = False
    BuyUnobtainedShip: bool = False
    BuyOrder: t.Tuple[LITERAL_MeritShop_BuyOrder, ...] = ('Cube',)


LITERAL_CoreShop_BuyOrder = t.Literal['Array']


class CoreShop(a.GroupBase):
    BuyOrder: t.Tuple[LITERAL_CoreShop_BuyOrder, ...] = ('Array',)
