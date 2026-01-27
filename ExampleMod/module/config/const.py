from alasio.config.entry.const import ConfigConst as ConfigConst_, ModEntryInfo
from alasio.config_dev.gen_index import IndexGenerator
from alasio.ext.env import set_project_root
from alasio.ext.path import PathStr
from alasio.logger import logger

entry = ModEntryInfo(name='example_mod')
entry.root = PathStr.new(__file__).uppath(3)


class ConfigConst(ConfigConst_):
    SCHEDULER_PRIORITY = """
    RestartDevice > RestartGame
    > OpsiCrossMonth
    > Commission > Tactical > Research
    > Exercise
    > Dorm > Meowfficer > Guild > Gacha
    > Reward
    > ShopFrequent > ShopOnce > Shipyard > Freebies
    > PrivateQuarters
    > OpsiExplore
    > Minigame > Awaken
    > OpsiAshBeacon
    > OpsiDaily > OpsiShop > OpsiVoucher
    > OpsiAbyssal > OpsiStronghold > OpsiObscure > OpsiArchive
    > Daily > Hard > OpsiAshBeacon > OpsiAshAssist > OpsiMonthBoss
    > Sos > EventSp > EventA > EventB > EventC > EventD
    > RaidDaily > CoalitionSp > WarArchives > MaritimeEscort
    > Event > Event2 > Raid > Hospital > Coalition > Main > Main2 > Main3
    > OpsiMeowfficerFarming
    > GemsFarming
    > OpsiHazard1Leveling
    """


if __name__ == '__main__':
    set_project_root(__file__, up=4)
    # generate
    logger.info(f'ModEntry: {entry}')
    self = IndexGenerator(entry)
    self.generate()
