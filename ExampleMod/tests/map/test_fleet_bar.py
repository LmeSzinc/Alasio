"""
Screenshot golden tests of the fleet bar of the map fleet preparation page.

The body of the module under test is the game script code, with two adaptations:
the imports point to alasio, and `FleetOption` carries constructor defaults and
`__eq__`, so a golden test compares the options as values:

- `module/map/fleet_bar.py`, the detector of the fleet bar options
- `module/map/map_fleet_preparation.py`, `FleetOperator`

The example mod carries the small asset layer the game script code uses
(`module/map/button.py`, `module/map/assets.py`), instead of the base classes of
the game script: the game script asset layer works on the same screenshot areas
and images, only what the recognition needs is here, see the module docstring of
`module/map/button.py`.

The game script modules import themselves as "module.xxx", the same way the mod
runtime imports them, see ExampleMod/tests/conftest.py.

The screenshots are collected by hand under ExampleMod/collection, they are
stored as lossless WebP, which keeps them small enough to live in the repository
while decoding to the very pixels of the original screenshot.

Run the tests from the repository root:

    python -m pytest ExampleMod/tests/map/test_fleet_bar.py -s

The golden values below are the output of the game script code on the
screenshots, read back by a human against the screenshots before they were
written down:

- `not_expanded.webp`: the fleet bar is closed
- `infobar_fleet1_expanded.webp`: the fleet bar of fleet 1 is opened, the info
  bar covers the left side of the first two options, fleet 1 is in use
- `fleet2_expanded.webp`: the fleet bar of fleet 2 is opened, options 5 and 6 are
  highlighted on this screenshot
- `only_2_fleets_fleet1_expanded.webp`: two fleets on the account, the screenshot
  is 1272x716 instead of 1280x720
- `submarine_expanded.webp`: the fleet bar of the submarine is opened, submarine 1
  is in use
"""
from module.base.base import ModuleBase
from module.map.assets import (
    FLEET_1_ADVICE, FLEET_1_BAR, FLEET_1_CHOOSE, FLEET_1_CLEAR, FLEET_1_HARD_SATIESFIED, FLEET_1_IN_USE, FLEET_2_BAR,
    FLEET_2_CHOOSE, SUBMARINE_ADVICE, SUBMARINE_BAR, SUBMARINE_CHOOSE, SUBMARINE_CLEAR, SUBMARINE_HARD_SATIESFIED,
    SUBMARINE_IN_USE
)
from module.map.fleet_bar import FleetBarDetector, FleetOption
from module.map.map_fleet_preparation import FleetOperator

from alasio.testing.screenshot import image


class TestFleetBarNotExpanded:
    """
    The fleet bar is closed on the screenshot, there is no option to detect.
    """

    @image("map/fleet_bar/not_expanded.webp")
    def test_not_expanded(self, main: ModuleBase):
        assert FleetBarDetector(main, bar=FLEET_1_BAR, choose=FLEET_1_CHOOSE).options == {}
        assert FleetBarDetector(main, bar=FLEET_2_BAR, choose=FLEET_2_CHOOSE).options == {}


class TestFleetBarFleet1Expanded:
    """
    The fleet bar of fleet 1 is opened, the info bar covers the left side of the
    first two options, so they are narrower than the 169px of a full option.

    The same screenshot reads the state of the fleet with `FleetOperator`: the
    options of the opened fleet bar, if the fleet can be cleared or recommended,
    and if it is the fleet in use.

    `is_hard_satisfied()` is None on this screenshot: `is_hard()` is False, the
    stage is not a hard mode, so the hard mode detection, which needs scipy, is
    never reached.
    """

    @image("map/fleet_bar/infobar_fleet1_expanded.webp")
    def test_fleet1_expanded(self, main: ModuleBase):
        options = FleetBarDetector(main, bar=FLEET_1_BAR, choose=FLEET_1_CHOOSE).options

        assert options == {
            1: FleetOption(area=(1034, 270, 1182, 304), index=1, selected=True),
            2: FleetOption(area=(1080, 312, 1182, 346), index=2, selected=False),
            3: FleetOption(area=(1013, 354, 1182, 388), index=3, selected=False),
            4: FleetOption(area=(1013, 396, 1182, 430), index=4, selected=False),
            5: FleetOption(area=(1013, 438, 1182, 473), index=5, selected=False),
            6: FleetOption(area=(1013, 480, 1182, 515), index=6, selected=False),
        }

        fleet_1 = FleetOperator(
            choose=FLEET_1_CHOOSE, advice=FLEET_1_ADVICE, bar=FLEET_1_BAR, clear=FLEET_1_CLEAR,
            in_use=FLEET_1_IN_USE, hard_satisfied=FLEET_1_HARD_SATIESFIED, main=main)

        fleet_1.update_options()

        assert fleet_1.options == options
        assert fleet_1.allow() is True
        assert fleet_1.is_hard() is False
        assert fleet_1.is_hard_satisfied() is None
        # in_use() compares with numpy, it returns a numpy bool
        assert bool(fleet_1.in_use()) is True


class TestFleetBarFleet2Expanded:
    """
    The fleet bar of fleet 2 is opened, all the options are at full width, and
    the options 5 and 6 are the ones highlighted on the screenshot.
    """

    @image("map/fleet_bar/fleet2_expanded.webp")
    def test_fleet2_expanded(self, main: ModuleBase):
        options = FleetBarDetector(main, bar=FLEET_2_BAR, choose=FLEET_2_CHOOSE).options

        assert options == {
            1: FleetOption(area=(1013, 384, 1182, 418), index=1, selected=False),
            2: FleetOption(area=(1013, 426, 1182, 460), index=2, selected=False),
            3: FleetOption(area=(1013, 468, 1182, 502), index=3, selected=False),
            4: FleetOption(area=(1013, 510, 1182, 544), index=4, selected=False),
            5: FleetOption(area=(1013, 552, 1182, 586), index=5, selected=True),
            6: FleetOption(area=(1013, 594, 1182, 628), index=6, selected=True),
        }

        # The options are stacked from the top of the fleet bar, one option per
        # fleet, 42 pixels apart.
        assert [option.area[1] for option in options.values()] == [384, 426, 468, 510, 552, 594]


class TestFleetBarTwoFleets:
    """
    The account has two fleets, and the screenshot is 1272x716 instead of
    1280x720, the assets of the game script still detect on it.
    """

    @image("map/fleet_bar/only_2_fleets_fleet1_expanded.webp")
    def test_two_fleets(self, main: ModuleBase):
        options = FleetBarDetector(main, bar=FLEET_1_BAR, choose=FLEET_1_CHOOSE).options

        assert options == {
            1: FleetOption(area=(1012, 269, 1175, 302), index=1, selected=True),
            2: FleetOption(area=(1012, 309, 1176, 344), index=2, selected=False),
        }


class TestFleetBarSubmarineExpanded:
    """
    The fleet bar of the submarine is opened, submarine 1 is in use.
    """

    @image("map/fleet_bar/submarine_expanded.webp")
    def test_submarine_expanded(self, main: ModuleBase):
        submarine = FleetOperator(
            choose=SUBMARINE_CHOOSE, advice=SUBMARINE_ADVICE, bar=SUBMARINE_BAR, clear=SUBMARINE_CLEAR,
            in_use=SUBMARINE_IN_USE, hard_satisfied=SUBMARINE_HARD_SATIESFIED, main=main)

        submarine.update_options()

        assert submarine.options == {
            1: FleetOption(area=(1013, 520, 1182, 554), index=1, selected=True),
            2: FleetOption(area=(1012, 562, 1182, 597), index=2, selected=False),
        }
        assert submarine.allow() is True
        # in_use() compares with numpy, it returns a numpy bool
        assert bool(submarine.in_use()) is True
