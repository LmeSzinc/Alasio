"""
Tests of IndexGenerator._generate() as the orchestrator of the generators.

The generated files are covered by the subdirectories, this file only checks
the order of the steps that belongs to IndexGenerator itself.
"""
from alasio.config_dev.gen_index import IndexGenerator
from alasio.ext.path.calc import joinpath
from alasio.testing.filesystem import fs  # noqa: F401
from tests.config_dev.gen.helpers import make_entry, nav_dir


def build(fs, navs):
    """
    Build an IndexGenerator over a mod of the fake filesystem.

    Args:
        fs (FakeFilesystem): The in-memory filesystem fixture
        navs (dict[str, str]): key: nav name, value of {nav}.args.yaml

    Returns:
        IndexGenerator:
    """
    entry = make_entry(fs)
    for nav, yaml in navs.items():
        fs.create_file(joinpath(nav_dir(fs, nav), f'{nav}.args.yaml'), contents=yaml)
    gen = IndexGenerator(entry)
    gen._generate()
    return gen


class TestModelIndex:
    def test_written_without_any_task(self, fs):
        """A mod without a task still gets the plan of its model files."""
        gen = build(fs, {
            'main': 'Campaign:\n  args:\n    Name: main\n',
            'gems': 'GemsCampaign:\n  parent: Campaign\n  args:\n    Name: gems\n',
        })
        assert gen.model_index_file.atomic_read_text() == """\
{
  "main/main_model.py": {},
  "gems/gems_model.py": {
    "main/main_model.py": ["Campaign"]
  }
}"""

    def test_written_after_the_model_files(self, fs):
        """The plan lists a model file the same run has just generated."""
        gen = build(fs, {'main': 'Campaign:\n  args:\n    Name: main\n'})
        assert gen.dict_nav_config['main'].model_file.exists()
        assert list(gen.model_index_data) == ['main/main_model.py']

    def test_removed_nav_leaves_the_plan(self, fs):
        """A nav whose groups are all removed leaves the plan with its model.

        The files of a previous generation are written by hand here: a stale
        model file, its i18n and the plan that listed it.
        """
        folder = nav_dir(fs, 'main')
        fs.create_file(joinpath(folder, 'main.args.yaml'), contents='Info:\n')
        fs.create_file(joinpath(folder, 'main_model.py'), contents='class Removed:\n    pass\n')
        fs.create_file(joinpath(folder, 'main_i18n.json'), contents='{"Removed": {"_info": {}}}')
        gen = IndexGenerator(make_entry(fs))
        fs.create_file(gen.model_index_file, contents='{"main/main_model.py": {}}')

        gen._generate()

        # the model of a nav without a class is stale, so the plan has no entry
        # left either: a plan listing a deleted file would be a broken import
        assert not gen.dict_nav_config['main'].model_file.exists()
        assert not gen.model_index_file.exists()
