/**
 * The mock data of the golden test review demo.
 *
 * The review page of the dev tools reads a mod through the backend, and the
 * topics of the review only serve the desktop client: the layout can not be
 * worked on without running the whole app. The demo page renders the same
 * workspace over these constants instead, taken from the screenshot tests of a
 * real mod, so the tree, the sources and the bindings have the shape the
 * backend sends.
 *
 * The `start_line` of an image is the line of its `@image` decorator and its
 * `end_line` the last line of the test, both 1-based and relative to the
 * source template, the same numbers `alasio.testing.preview.parse_unittest`
 * reports.
 */
import type { GoldenTestEntry, GoldenTestFileData, UnittestImage } from "$private/dev/golden-test/types";

/** The mod the demo browses, the panel is fixed to it */
export const DEMO_MOD = "alasio";

/** The file the page opens with */
export const DEMO_SELECTED = "map/test_map_fleet_preparation.py";

const PREPARATION_SOURCE = `"""
Screenshot tests for fleet preparation.

The screenshots of collection/map/fleet_bar cover the fleet bar in the normal
and W15 layouts, with and without the info bar, including a half loaded info
bar. Each test binds one screenshot and asserts the recognition result the
screenshot is collected for.
"""
import pytest
from alasio.testing.screenshot import image

from module.config.server import set_server
from module.logger import logger
from module.map.fleet_bar import FleetOption
from module.map.map_fleet_preparation import FleetPreparation


@pytest.fixture(autouse=True)
def capture_logs():
    """
    Capture the logs of fleet bar recognition, so a test run does not write
    them to the log file.
    """
    with logger.mock_capture_writer():
        yield


def build_fleets(main, server='cn'):
    """
    Build the fleet operators of the screenshot on the given server.

    Args:
        main (FleetPreparation): Module under test.
        server (str): Game server of the screenshot.

    Returns:
        tuple[FleetOperator, FleetOperator, FleetOperator]:
            fleet_1, fleet_2 and submarine operators.
    """
    set_server(server)
    return main.fleet_operators()


class TestFleetBarNotExpanded:
    @image("map/fleet_bar/not_expanded.png")
    def test_not_expanded(self, main: FleetPreparation):
        assert main.info_bar_count() == 0
        fleet_1, fleet_2, submarine = build_fleets(main)
        assert fleet_1.in_use()
        assert fleet_2.in_use()
        assert submarine.in_use()
        fleet_1.update_options()
        assert fleet_1.options_error == ''
        fleet_2.update_options()
        assert fleet_2.options_error == ''
        submarine.update_options()
        assert submarine.options_error == ''
        assert fleet_1.options == {}
        assert fleet_2.options == {}
        assert submarine.options == {}

    @image("map/fleet_bar/w15_not_expanded.png")
    def test_w15_not_expanded(self, main: FleetPreparation):
        assert main.info_bar_count() == 0
        fleet_1, fleet_2, submarine = build_fleets(main)
        assert fleet_2._in_use.name == 'FLEET_2_IN_USE_W15'
        assert fleet_1.in_use()
        assert fleet_2.in_use()
        assert submarine.in_use()
        fleet_1.update_options()
        assert fleet_1.options_error == ''
        fleet_2.update_options()
        assert fleet_2.options_error == ''
        submarine.update_options()
        assert submarine.options_error == ''
        assert fleet_1.options == {}
        assert fleet_2.options == {}
        assert submarine.options == {}


class TestFleetBarExpanded:
    @image("map/fleet_bar/fleet1_expanded.png")
    def test_fleet1_expanded(self, main: FleetPreparation):
        assert main.info_bar_count() == 0
        fleet_1, _, _ = build_fleets(main)
        assert fleet_1.allow()
        assert fleet_1.in_use()
        fleet_1.update_options()
        assert fleet_1.options_error == ''
        assert len(fleet_1.options) == 6
        assert fleet_1.options == {
            1: FleetOption(area=(1013, 270, 1174, 304), index=1, selected=False),
            2: FleetOption(area=(1013, 312, 1179, 346), index=2, selected=False),
            3: FleetOption(area=(1013, 354, 1179, 388), index=3, selected=False),
            4: FleetOption(area=(1013, 396, 1179, 430), index=4, selected=False),
            5: FleetOption(area=(1156, 438, 1178, 471), index=5, selected=True),
            6: FleetOption(area=(1156, 480, 1179, 513), index=6, selected=True),
        }

    @image("map/fleet_bar/fleet2_expanded.png")
    def test_fleet2_expanded(self, main: FleetPreparation):
        assert main.info_bar_count() == 0
        _, fleet_2, _ = build_fleets(main)
        assert fleet_2._in_use.name == 'FLEET_2_IN_USE'
        assert fleet_2.allow()
        assert fleet_2.in_use()
        fleet_2.update_options()
        assert fleet_2.options_error == ''
        assert len(fleet_2.options) == 6
        assert fleet_2.options == {
            1: FleetOption(area=(1013, 384, 1174, 418), index=1, selected=False),
            2: FleetOption(area=(1013, 426, 1179, 460), index=2, selected=False),
            3: FleetOption(area=(1013, 468, 1179, 502), index=3, selected=False),
            4: FleetOption(area=(1013, 510, 1179, 544), index=4, selected=False),
            5: FleetOption(area=(1156, 552, 1178, 585), index=5, selected=True),
            6: FleetOption(area=(1156, 594, 1179, 627), index=6, selected=True),
        }`;

const FLEET_SOURCE = `"""
Screenshot tests for the fleet list of the fleet preparation page.

Every test binds one screenshot of collection/map/fleet and asserts the
recognition result the screenshot is collected for.
"""
import pytest
from alasio.testing.screenshot import image

from module.logger import logger
from module.map.fleet import Fleet


@pytest.fixture(autouse=True)
def capture_logs():
    """
    Capture the logs of the fleet recognition, so a test run does not write them
    to the log file.
    """
    with logger.mock_capture_writer():
        yield


class TestFleetList:
    @image("map/fleet/fleet_list.png")
    def test_fleet_list(self, main: Fleet):
        assert main.fleet_count() == 6
        names = main.fleet_names()
        assert len(names) == 6
        assert names[0] == 'Fleet 1'

    @image()
    def test_without_screenshot(self, main: Fleet):
        # The decorator binds no screenshot, so this test has no image column
        assert main.fleet_count() == 0`;

const CAMPAIGN_SOURCE = `"""
Screenshot tests for the resource status bar.

The screenshots of collection/campaign/status cover the two status bars of the
game. Every test binds one screenshot and asserts the amounts read from it.
"""
import pytest
from alasio.testing.screenshot import image

from module.campaign.campaign_status import CampaignStatus, StatusAmount
from module.logger import logger


@pytest.fixture(autouse=True)
def capture_logs():
    """
    Capture the logs of the status bar recognition, so a test run does not write
    them to the log file.
    """
    with logger.mock_capture_writer():
        yield


class TestStatusBarOldUI:
    @image('campaign/status/campaign.png')
    def test_campaign(self, main: CampaignStatus):
        assert main.get_campaign_status(oil=True, coin=True, gem=True) == {
            'oil': StatusAmount(value=20052, total=19200),
            'coin': StatusAmount(value=458447, total=114900),
            'gem': StatusAmount(value=14124, total=None),
        }

    @image('campaign/status/main.png')
    def test_main(self, main: CampaignStatus):
        assert main.get_campaign_status(oil=True, coin=True, gem=True) == {
            'oil': StatusAmount(value=20052, total=19200),
            'coin': StatusAmount(value=458447, total=114900),
            'gem': StatusAmount(value=14124, total=None),
        }`;

/** The tree the page opens with, `map` is the folder open on mount */
export const DEMO_TREE: Record<string, GoldenTestEntry[]> = {
  "": [
    { name: "campaign", path: "campaign", is_dir: true },
    { name: "map", path: "map", is_dir: true },
  ],
  campaign: [{ name: "test_campaign_status.py", path: "campaign/test_campaign_status.py" }],
  map: [
    { name: "test_map_fleet.py", path: "map/test_map_fleet.py" },
    { name: "test_map_fleet_preparation.py", path: "map/test_map_fleet_preparation.py" },
  ],
};

const PREPARATION_IMAGES: UnittestImage[] = [
  {
    class_name: "TestFleetBarNotExpanded",
    func_name: "test_not_expanded",
    start_line: 45,
    end_line: 60,
    image: "map/fleet_bar/not_expanded.png",
  },
  {
    class_name: "TestFleetBarNotExpanded",
    func_name: "test_w15_not_expanded",
    start_line: 62,
    end_line: 78,
    image: "map/fleet_bar/w15_not_expanded.png",
  },
  {
    class_name: "TestFleetBarExpanded",
    func_name: "test_fleet1_expanded",
    start_line: 82,
    end_line: 98,
    image: "map/fleet_bar/fleet1_expanded.png",
  },
  {
    class_name: "TestFleetBarExpanded",
    func_name: "test_fleet2_expanded",
    start_line: 100,
    end_line: 117,
    image: "map/fleet_bar/fleet2_expanded.png",
  },
];

const FLEET_IMAGES: UnittestImage[] = [
  {
    class_name: "TestFleetList",
    func_name: "test_fleet_list",
    start_line: 25,
    end_line: 30,
    image: "map/fleet/fleet_list.png",
  },
  {
    class_name: "TestFleetList",
    func_name: "test_without_screenshot",
    start_line: 32,
    end_line: 35,
    // An `@image` without a path is kept as a binding without an image: the
    // page renders the error in the column of the screenshots
    image: "",
    error: "line 32: @image path is empty",
  },
];

const CAMPAIGN_IMAGES: UnittestImage[] = [
  {
    class_name: "TestStatusBarOldUI",
    func_name: "test_campaign",
    start_line: 25,
    end_line: 31,
    image: "campaign/status/campaign.png",
  },
  {
    class_name: "TestStatusBarOldUI",
    func_name: "test_main",
    start_line: 33,
    end_line: 39,
    image: "campaign/status/main.png",
  },
];

/** The test files of the tree, every file of the tree has its data here */
export const DEMO_FILES: Record<string, GoldenTestFileData> = {
  "map/test_map_fleet_preparation.py": {
    mod_name: DEMO_MOD,
    file: "map/test_map_fleet_preparation.py",
    source: PREPARATION_SOURCE,
    images: PREPARATION_IMAGES,
  },
  "map/test_map_fleet.py": {
    mod_name: DEMO_MOD,
    file: "map/test_map_fleet.py",
    source: FLEET_SOURCE,
    images: FLEET_IMAGES,
  },
  "campaign/test_campaign_status.py": {
    mod_name: DEMO_MOD,
    file: "campaign/test_campaign_status.py",
    source: CAMPAIGN_SOURCE,
    images: CAMPAIGN_IMAGES,
  },
};
