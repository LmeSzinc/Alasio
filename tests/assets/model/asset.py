from alasio.assets.template import Asset, Template

# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m dev_tools.button_extract ```

_path_ = 'assets/model'
# This is a battle preparation button
# Used in the main menu
BATTLE_PREPARATION = Asset(
    path=_path_, name='BATTLE_PREPARATION',
    search=(100, 100, 200, 200),
    button=(100, 100, 200, 200),
    match=Template.match_template,
    similarity=0.75,
    colordiff=10,
    template=lambda: (
        Template(
            area=(100, 100, 200, 200), color=(234, 245, 248),
            # source='~BATTLE_PREPARATION.png'
        ),
        Template(
            lang='en', area=(100, 100, 200, 200), color=(234, 245, 248),
            search=(100, 100, 200, 200),
            match=Template.match_template,
            # source='~BATTLE_PREPARATION.png'
        ),
        Template(
            lang='en', frame=2, area=(100, 100, 200, 200), color=(234, 245, 248),
            # source='~BATTLE_PREPARATION.png'
        ),
    ),
    # ref='~Screenshot_123.png'
    #ref = '~Screenshot_456.png'
)
