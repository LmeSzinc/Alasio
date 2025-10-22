import pytest

from alasio.assets.model.parser import AssetParser, MetaAsset, MetaTemplate
from alasio.assets.template import Asset, Template
from alasio.ext.path import PathStr


class TestAssetParserBasics:
    """Test basic parsing functionality"""

    def test_parse_simple_asset(self):
        """Test parsing a simple Asset definition"""
        code = """
SIMPLE_ASSET = Asset(
    path='assets/test',
    name='SIMPLE_ASSET',
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        assert len(assets) == 1
        asset = assets[0]
        assert isinstance(asset, MetaAsset)
        assert isinstance(asset, Asset)
        assert asset.meta_asset_name == 'SIMPLE_ASSET'
        assert asset.path == 'assets/test'
        assert asset.name == 'SIMPLE_ASSET'

    def test_parse_asset_with_all_parameters(self):
        """Test parsing Asset with all optional parameters"""
        code = """
FULL_ASSET = Asset(
    path='assets/model',
    name='FULL_ASSET',
    search=(100, 200, 300, 400),
    button=(150, 250, 350, 450),
    similarity=0.85,
    colordiff=15,
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        assert len(assets) == 1
        asset = assets[0]
        assert asset.search == (100, 200, 300, 400)
        assert asset.button == (150, 250, 350, 450)
        assert asset.similarity == 0.85
        assert asset.colordiff == 15

    def test_parse_multiple_assets(self):
        """Test parsing multiple Asset definitions"""
        code = """
ASSET_ONE = Asset(path='assets/a', name='ASSET_ONE')
ASSET_TWO = Asset(path='assets/b', name='ASSET_TWO')
ASSET_THREE = Asset(path='assets/c', name='ASSET_THREE')
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        assert len(assets) == 3
        assert assets[0].meta_asset_name == 'ASSET_ONE'
        assert assets[1].meta_asset_name == 'ASSET_TWO'
        assert assets[2].meta_asset_name == 'ASSET_THREE'


class TestTemplatesParsing:
    """Test Template parsing within Assets"""

    def test_parse_single_template(self):
        """Test parsing Asset with single Template"""
        code = """
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
    template=lambda: (
        Template(
            area=(10, 20, 30, 40),
            color=(255, 128, 64),
        ),
    ),
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        assert len(assets) == 1
        assert len(assets[0].meta_templates) == 1

        template = assets[0].meta_templates[0]
        assert isinstance(template, MetaTemplate)
        assert isinstance(template, Template)
        assert template.area == (10, 20, 30, 40)
        assert template.color == (255, 128, 64)

    def test_parse_multiple_templates(self):
        """Test parsing Asset with multiple Templates"""
        code = """
MULTI_TEMPLATE = Asset(
    path='assets/test',
    name='MULTI_TEMPLATE',
    template=lambda: (
        Template(area=(1, 2, 3, 4), color=(255, 0, 0)),
        Template(area=(5, 6, 7, 8), color=(0, 255, 0), lang='en'),
        Template(area=(9, 10, 11, 12), color=(0, 0, 255), lang='zh', frame=2),
    ),
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        templates = assets[0].meta_templates
        assert len(templates) == 3

        assert templates[0].area == (1, 2, 3, 4)
        assert templates[1].area == (5, 6, 7, 8)
        assert templates[1].lang == 'en'
        assert templates[2].area == (9, 10, 11, 12)
        assert templates[2].lang == 'zh'
        assert templates[2].frame == 2


class TestCommentsParsing:
    """Test parsing of commented attributes"""

    def test_parse_preceding_comments(self):
        """Test parsing comments before Asset definition"""
        code = """
# This is the first comment
# This is the second comment
# This is the third comment
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        comments = assets[0].meta_asset_doc
        assert comments == "This is the first comment\nThis is the second comment\nThis is the third comment"

    def test_parse_commented_ref_with_space(self):
        """Test parsing commented ref attribute with space after #"""
        code = """
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
    # ref='~Screenshot_1.png'
    # ref='~Screenshot_2.png'
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        refs = assets[0].meta_ref
        assert len(refs) == 2
        assert refs[0] == "~Screenshot_1.png"
        assert refs[1] == "~Screenshot_2.png"

    def test_parse_commented_ref_without_space(self):
        """Test parsing commented ref attribute without space after #"""
        code = """
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
    #ref='~Screenshot_1.png'
    #ref='~Screenshot_2.png'
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        refs = assets[0].meta_ref
        assert len(refs) == 2
        assert refs[0] == "~Screenshot_1.png"
        assert refs[1] == "~Screenshot_2.png"

    def test_parse_commented_ref_with_double_quotes(self):
        """Test parsing commented ref with double quotes"""
        code = """
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
    # ref="~Screenshot_1.png"
    #ref="~Screenshot_2.png"
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        refs = assets[0].meta_ref
        assert len(refs) == 2
        assert refs[0] == "~Screenshot_1.png"
        assert refs[1] == "~Screenshot_2.png"

    def test_parse_commented_source(self):
        """Test parsing commented source attribute in Template"""
        code = """
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
    template=lambda: (
        Template(
            area=(10, 20, 30, 40),
            color=(255, 128, 64),
            # source='~template_1.png'
        ),
        Template(
            area=(50, 60, 70, 80),
            color=(128, 255, 64),
            #source='~template_2.png'
        ),
    ),
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        templates = assets[0].meta_templates
        assert templates[0].meta_source == "~template_1.png"
        assert templates[1].meta_source == "~template_2.png"

    def test_parse_commented_source_only_first(self):
        """Test that only first commented source is captured"""
        code = """
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
    template=lambda: (
        Template(
            area=(10, 20, 30, 40),
            color=(255, 128, 64),
            # source='~first.png'
            # source='~second.png'
        ),
    ),
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        template = assets[0].meta_templates[0]
        assert template.meta_source == "~first.png"


class TestMetadata:
    """Test metadata attributes"""

    def test_asset_line_numbers(self):
        """Test Asset line number metadata"""
        code = """
# Line 2
# Line 3
ASSET_ONE = Asset(  # Line 4
    path='assets/test',  # Line 5
    name='ASSET_ONE',  # Line 6
)  # Line 7
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        asset = assets[0]
        assert asset.meta_start_line == 4
        assert asset.meta_end_line == 7

    def test_template_line_numbers(self):
        """Test Template line number metadata"""
        code = """
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
    template=lambda: (
        Template(  # Line 5
            area=(10, 20, 30, 40),
            color=(255, 128, 64),
        ),  # Line 8
    ),
)
"""
        parser = AssetParser(code)
        assets = parser.parse_assets()

        template = assets[0].meta_templates[0]
        assert template.meta_start_line == 6  # Template( line
        assert template.meta_end_line == 9  # ), line


class TestErrorHandling:
    """Test error handling for invalid inputs"""

    def test_missing_path_parameter(self):
        """Test that missing path parameter raises TypeError"""
        code = """
INVALID_ASSET = Asset(
    name='INVALID_ASSET',
    similarity=0.80,
)
"""
        parser = AssetParser(code)
        with pytest.raises(TypeError, match="missing required argument 'path'"):
            parser.parse_assets()

    def test_missing_name_parameter(self):
        """Test that missing name parameter raises TypeError"""
        code = """
INVALID_ASSET = Asset(
    path='assets/test',
    similarity=0.80,
)
"""
        parser = AssetParser(code)
        with pytest.raises(TypeError, match="missing required argument 'name'"):
            parser.parse_assets()

    def test_template_missing_area(self):
        """Test that Template missing area raises TypeError"""
        code = """
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
    template=lambda: (
        Template(
            color=(255, 128, 64),
        ),
    ),
)
"""
        parser = AssetParser(code)
        with pytest.raises(TypeError, match="Template at line"):
            parser.parse_assets()

    def test_template_missing_color(self):
        """Test that Template missing color raises TypeError"""
        code = """
TEST_ASSET = Asset(
    path='assets/test',
    name='TEST_ASSET',
    template=lambda: (
        Template(
            area=(10, 20, 30, 40),
        ),
    ),
)
"""
        parser = AssetParser(code)
        with pytest.raises(TypeError, match="Template at line"):
            parser.parse_assets()


class TestComplexScenarios:
    """Test complex real-world scenarios"""

    def test_parse_real_asset_file(self):
        """Test parsing real asset.py file in the same directory"""
        # Get the directory of this test file
        asset_file = PathStr.new(__file__).uppath() / 'asset.py'

        # Read the asset.py file
        code = asset_file.atomic_read_text()

        parser = AssetParser(code)
        assets = parser.parse_assets()

        assert len(assets) == 1
        asset = assets[0]

        # Check basic properties
        assert asset.meta_asset_name == 'BATTLE_PREPARATION'
        assert asset.path == '_path_'
        assert asset.name == 'BATTLE_PREPARATION'
        assert asset.similarity == 0.75
        assert asset.colordiff == 10
        assert asset.match == 'Template.match_template'

        # Check comments
        assert 'This is a battle preparation button' in asset.meta_asset_doc
        assert 'Used in the main menu' in asset.meta_asset_doc

        # Check commented refs (quotes should be stripped)
        assert len(asset.meta_ref) == 2
        assert "~Screenshot_123.png" in asset.meta_ref
        assert "~Screenshot_456.png" in asset.meta_ref

        # Check templates
        assert len(asset.meta_templates) == 3

        # First template with single quotes
        assert asset.meta_templates[0].meta_source == "~BATTLE_PREPARATION.png"

        # Second template with double quotes
        assert asset.meta_templates[1].lang == 'en'
        assert asset.meta_templates[1].meta_source == "~BATTLE_PREPARATION.png"

        # Third template
        assert asset.meta_templates[2].frame == 2
        assert asset.meta_templates[2].meta_source == "~BATTLE_PREPARATION.png"
