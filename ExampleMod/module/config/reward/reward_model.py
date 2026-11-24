import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

LITERAL_Commission_PresetFilter = t.Literal['cube', 'cube_24h', 'chip', 'chip_24h', 'oil', 'custom']
LITERAL_Commission_CustomFilter = t.Literal[
    'DailyEvent', 'Gem-4', 'Gem-2', 'Gem-8', 'ExtraCube-0:30', 'ExtraCube-1:30', 'ExtraCube-3', 'ExtraCube-4',
    'ExtraCube-5', 'ExtraCube-8', 'UrgentCube-1:30', 'UrgentCube-1:45', 'UrgentCube-3', 'UrgentCube-2:15',
    'UrgentCube-4', 'UrgentCube-6', 'ExtraDrill-5:20', 'ExtraDrill-2', 'ExtraDrill-3:20', 'ExtraDrill-1',
    'ExtraDrill-2:40', 'ExtraDrill-0:20', 'Major', 'DailyChip', 'DailyResource', 'ExtraPart-0:30', 'ExtraPart-1',
    'ExtraPart-1:30', 'ExtraOil-1', 'ExtraOil-4', 'ExtraOil-8', 'UrgentBox-6', 'UrgentBox-3', 'UrgentBox-1',
    'Extra-0:20', 'Extra-0:30', 'Extra-1:00', 'Extra-1:30', 'Extra-2:00', 'UrgentDrill-4', 'UrgentDrill-2:40',
    'UrgentDrill-2', 'UrgentDrill-1', 'UrgentDrill-1:30', 'UrgentDrill-1:10', 'shortest',
]


class Commission(a.GroupBase):
    PresetFilter: LITERAL_Commission_PresetFilter = 'cube'
    CustomFilter: t.Tuple[LITERAL_Commission_CustomFilter, ...] = (
        'DailyEvent', 'Gem-4', 'Gem-2', 'Gem-8', 'ExtraCube-0:30', 'UrgentCube-1:30', 'UrgentCube-1:45', 'UrgentCube-3',
        'ExtraDrill-5:20', 'ExtraDrill-2', 'ExtraDrill-3:20', 'UrgentCube-2:15', 'UrgentCube-4', 'ExtraDrill-1',
        'UrgentCube-6', 'ExtraCube-1:30', 'ExtraDrill-2:40', 'ExtraDrill-0:20', 'Major', 'DailyChip', 'DailyResource',
        'ExtraPart-0:30', 'ExtraOil-1', 'UrgentBox-6', 'ExtraCube-3', 'ExtraPart-1', 'UrgentBox-3', 'ExtraCube-4',
        'ExtraPart-1:30', 'ExtraOil-4', 'UrgentBox-1', 'ExtraCube-5', 'ExtraCube-8', 'ExtraOil-8', 'UrgentDrill-4',
        'UrgentDrill-2:40', 'UrgentDrill-2', 'UrgentDrill-1', 'UrgentDrill-1:30', 'UrgentDrill-1:10', 'Extra-0:20',
        'Extra-0:30', 'Extra-1:00', 'Extra-1:30', 'Extra-2:00', 'shortest',
    )
    DoMajorCommission: bool = False


LITERAL_Tactical_TacticalFilter = t.Literal[
    'Same-T4', 'Same-T3', 'Same-T2', 'Same-T1', 'Blue-T2', 'Blue-T3', 'Blue-T4', 'Blue-T1', 'Yellow-T2', 'Yellow-T3',
    'Yellow-T4', 'Yellow-T1', 'Red-T2', 'Red-T3', 'Red-T4', 'Red-T1', 'first',
]
LITERAL_Tactical_RapidTrainingSlot = t.Literal['do_not_use', 'slot_1', 'slot_2', 'slot_3', 'slot_4']


class Tactical(a.GroupBase):
    TacticalFilter: t.Tuple[LITERAL_Tactical_TacticalFilter, ...] = (
        'Same-T4', 'Same-T3', 'Same-T2', 'Same-T1', 'Blue-T2', 'Yellow-T2', 'Red-T2', 'Blue-T3', 'Yellow-T3', 'Red-T3',
        'Blue-T4', 'Yellow-T4', 'Red-T4', 'Blue-T1', 'Yellow-T1', 'Red-T1', 'first',
    )
    RapidTrainingSlot: LITERAL_Tactical_RapidTrainingSlot = 'do_not_use'


class ControlExpOverflow(a.GroupBase):
    Enable: bool = True
    T4Allow: int = 100
    T3Allow: int = 100
    T2Allow: int = 200
    T1Allow: int = 200


class AddNewStudent(a.GroupBase):
    Enable: bool = False
    Favorite: bool = True
    MinLevel: e.Annotated[int, m.Meta(ge=1, le=100)] = 50


LITERAL_Research_UseCube = t.Literal['always_use', 'only_05_hour', 'only_no_project', 'do_not_use']
LITERAL_Research_PresetFilter = t.Literal[
    'custom', 'series_9_blueprint_ta152', 'series_9_blueprint_only', 'series_9_ta152_only', 'series_8_blueprint_305',
    'series_8_blueprint_only', 'series_8_305_only', 'series_7_blueprint_la9', 'series_7_blueprint_only',
    'series_7_la9_only', 'series_6_blueprint_203', 'series_6_blueprint_only', 'series_6_203_only',
    'series_5_blueprint_152', 'series_5_blueprint_only', 'series_5_152_only', 'series_4_blueprint_tenrai',
    'series_4_blueprint_only', 'series_4_tenrai_only', 'series_3_blueprint_234', 'series_3_blueprint_only',
    'series_3_234_only', 'series_2_than_3_457_234', 'series_2_blueprint_457', 'series_2_blueprint_only',
    'series_2_457_only',
]

# Pattern of Research.CustomFilter: every item is a value decoded by one of its levels, or one of its commands
PATTERN_Research_CustomFilter = (
    '^(?:(?:(?P<series>S[1-9])?-?(?P<family>DR|PRY|maximmelmann|goudenleeuw|mecklenburg|hindenburg|saintlouis|valparais'
    'o|anchorage|champagne|friedrich|kearsarge|marcopolo|rupprecht|takahashi|cheshire|gascogne|kitakaze|nakhimov|plymou'
    'th|shimanto|vittorio|chkalov|flandre|georgia|hakuryu|halford|monarch|neptune|schultz|seattle|august|bayard|daisen|'
    'dmitri|duncan|harbin|kansas|napoli|azuma|brest|drake|ibuki|izumo|mainz|orage|agir|odin|roon|[BCDEGHQT])?-?(?P<deta'
    'il>\\d{3}|\\d\\.\\d|\\d\\d?)?)|(?:reset))$'
)


class Research(a.GroupBase):
    UseCube: LITERAL_Research_UseCube = 'only_05_hour'
    UseCoin: LITERAL_Research_UseCube = 'always_use'
    UsePart: LITERAL_Research_UseCube = 'always_use'
    AllowDelay: bool = True
    PresetFilter: LITERAL_Research_PresetFilter = 'series_9_blueprint_ta152'
    CustomFilter: t.Tuple[e.Annotated[str, m.Meta(pattern=PATTERN_Research_CustomFilter)], ...] = (
        'S9-DR-0.5', 'S9-PRY-0.5', 'S9-Q-0.5', 'S9-H-0.5', 'Q-0.5', 'S9-DR-2.5', 'S9-G-1.5', 'S9-Q-1', 'S9-DR-5', '0.5',
        'S9-G-4', 'S9-Q-2', 'S9-PRY-2.5', 'S8-E-880', 'S8-E-180', 'reset', 'S9-DR-8', 'Q-1', '1', 'S9-E-315',
        'S9-G-2.5', 'G-1.5', '1.5', 'S9-E-031', 'S9-Q-4', 'Q-2', 'E-2', '2', 'DR-2.5', 'PRY-2.5', 'G-2.5', '2.5',
        'S9-PRY-5', 'S9-PRY-8', 'Q-4', 'G-4', '4', 'S9-C-6', 'DR-5', 'PRY-5', '5', 'C-6', '6', 'S9-C-8', 'S9-C-12',
        'DR-8', 'PRY-8', 'C-8', '8', 'C-12', '12',
    )
