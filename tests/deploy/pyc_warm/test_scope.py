"""
Tests of the scope of a warmup, see alasio/deploy/pyc_warm/scope.py.
"""
import os
from types import SimpleNamespace

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import DeployTarget
from alasio.deploy.pyc_warm.scope import (
    TIER_COMMON, TIER_LOCK, TIER_MOD, TIER_PACK, Scope, ScopeResult, dist_infos, index_sources, localize_path,
    record_sources, site_packages_dirs
)
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack.conftest import make_repo

# The site-packages folder of the tests
SITE = '/site'


def write(fs, path, content=b''):
    """
    Create a file of the fake filesystem.

    Args:
        fs (FakeFilesystem): The fake filesystem
        path (str): Path of the file
        content (bytes | str): Content of the file

    Returns:
        str: Path of the file
    """
    fs.create_file(path, contents=content)
    return path


def make_index_pack(files, commit='c1'):
    """
    Build the index pack of a commit.

    Only called at import time: the mock repository reads the attributes
    of the real checkout, which an active fake filesystem does not serve.

    Args:
        files (dict[str, bytes]): Path to content of the files of the
            tree
        commit (str): Commit to build the pack of

    Returns:
        bytes: The index pack, header and index section
    """
    repo = make_repo({commit: files})
    return b''.join(PackFull(repo, commit=commit).iter_packidx_data())


# The index packs of the tests, built before any fake filesystem is active
MODULE_PACK = make_index_pack({
    'module/main.py': b'VALUE = 1\n',
    'module/data.json': b'{}\n',
    'module/deep/nested.py': b'VALUE = 2\n',
})
TRIO_PACK = make_index_pack({
    'trio/_abc.py': b'VALUE = 1\n',
    'trio/data.json': b'{}\n',
})
LOCALIZE_PACK = make_index_pack({
    '.pack/index.pack': b'x\n',
    '.pack/lock': b'x\n',
    'trio/_abc.py': b'x\n',
})


def make_dist(fs, name, record_paths, version='1.0', site=SITE):
    """
    Create the .dist-info folder of a distribution, with a RECORD.

    Args:
        fs (FakeFilesystem): The fake filesystem
        name (str): Name of the distribution, as pip builds the folder
        record_paths (list[str]): Paths of the RECORD rows, relative to
            site-packages
        version (str): Version of the distribution
        site (str): Posix path of the site-packages folder

    Returns:
        str: Posix path of the .dist-info folder
    """
    dist_info = f'{site}/{name}-{version}.dist-info'
    rows = ''.join(f'{path},sha256=abc,1\n' for path in record_paths)
    write(fs, f'{dist_info}/RECORD', rows.encode())
    return dist_info


class FakeMod:
    """
    A mod as the scope reads one: a name, a root and a declaration.

    Attributes:
        name (str): Name of the mod
        root (str): Posix path of the mod tree
        entry (SimpleNamespace): The declaration, only the fields the
            scope reads are set
    """

    def __init__(self, name, root, requirements_file='requirements.txt'):
        """
        Args:
            name (str): Name of the mod
            root (str): Posix path of the mod tree
            requirements_file (str): Declared location of the pins of the
                mod, relative to root
        """
        self.name = name
        self.root = root
        self.entry = SimpleNamespace(requirements_file=requirements_file)


def collect(results):
    """
    Read a generator of the scope into a list.

    Args:
        results (Iterable[ScopeResult]): The results of a tier

    Returns:
        list[ScopeResult]: The results, in the order they were yielded
    """
    return list(results)


def by_target(results):
    """
    Index the results of a tier by the unit they answer.

    Args:
        results (list[ScopeResult]): The results of a tier

    Returns:
        dict[str, list[str]]: Target to its sources
    """
    return {result.target: result.sources for result in results}


class TestLocalizePath:
    """
    Tests for localize_path().
    """

    @pytest.mark.parametrize('path, name, expected', [
        # The project tree target has the pack area as its ledger folder
        ('.pack/index.pack', '', '.pack/index.pack'),
        ('module/main.py', '', 'module/main.py'),
        # A named target maps its pack area into its own ledger folder
        ('module/main.py', 'trio', 'module/main.py'),
        ('trio/_abc.py', 'trio', 'trio/_abc.py'),
        ('.pack/index.pack', 'trio', '.pack/trio/index.pack'),
        ('.pack/workspace/job.pack', 'trio', '.pack/trio/workspace/job.pack'),
        # A path that only looks like the pack area is not a pack record
        ('.packages/x.py', 'trio', '.packages/x.py'),
    ])
    def test_path(self, path, name, expected):
        """Only the pack area records of a named target are rewritten."""
        assert localize_path(path, name) == expected

    def test_same_as_the_deploy_target(self):
        """The mapping is the one of DeployTarget.localize()."""
        canonical = sorted(PackDecodeBase(LOCALIZE_PACK).fileinfo)
        decoder = PackDecodeBase(LOCALIZE_PACK)
        DeployTarget(root='/unused', name='trio').localize(decoder)
        assert sorted(decoder.fileinfo) == sorted(localize_path(path, 'trio') for path in canonical)


class TestIndexSources:
    """
    Tests for index_sources().
    """

    def test_missing(self, fs):
        """A target without an index pack has no file list."""
        fs.create_dir('/mod/.pack')
        assert index_sources('/mod') is None

    def test_corrupt(self, fs):
        """An index pack that can not be decoded is no file list."""
        write(fs, '/mod/.pack/index.pack', b'garbage')
        assert index_sources('/mod') is None

    def test_empty_folder(self, fs):
        """A target that was never created has no file list."""
        assert index_sources('/mod') is None

    def test_sources(self, fs):
        """Only the .py records are sources, as absolute paths."""
        write(fs, '/mod/.pack/index.pack', MODULE_PACK)
        assert sorted(index_sources('/mod')) == ['/mod/module/deep/nested.py', '/mod/module/main.py']

    def test_named_target(self, fs):
        """The ledger of a named target is its own pack area folder."""
        write(fs, '/site/.pack/trio/index.pack', TRIO_PACK)
        assert index_sources('/site', 'trio') == ['/site/trio/_abc.py']


class TestDistInfos:
    """
    Tests for dist_infos().
    """

    def test_names(self, fs):
        """The folder name is read the way pip builds it."""
        for folder in (
            'trio-0.22.0.dist-info',
            'ruamel.yaml-0.17.21.dist-info',
            'cached_property-1.5.1.dist-info',
            'opencv_python_headless-4.8.0.76.dist-info',
        ):
            write(fs, f'{SITE}/{folder}/METADATA', b'Name: x\n')
        assert dist_infos(SITE) == {
            'trio': f'{SITE}/trio-0.22.0.dist-info',
            'ruamel-yaml': f'{SITE}/ruamel.yaml-0.17.21.dist-info',
            'cached-property': f'{SITE}/cached_property-1.5.1.dist-info',
            'opencv-python-headless': f'{SITE}/opencv_python_headless-4.8.0.76.dist-info',
        }

    def test_other_entries_ignored(self, fs):
        """A folder that is not a .dist-info is not a distribution."""
        write(fs, f'{SITE}/trio/__init__.py', b'')
        write(fs, f'{SITE}/legacy-1.0.egg-info/PKG-INFO', b'')
        write(fs, f'{SITE}/module.py', b'')
        assert dist_infos(SITE) == {}

    def test_missing(self, fs):
        """A folder that does not exist holds no distribution."""
        assert dist_infos('/missing') == {}


class TestRecordSources:
    """
    Tests for record_sources().
    """

    def test_sources(self, fs):
        """The .py rows of the RECORD are the sources of the installation."""
        dist_info = make_dist(fs, 'trio', ['trio/_abc.py', 'trio/_core/_run.py', 'trio/_core.pyd'])
        assert record_sources(SITE, dist_info) == [
            f'{SITE}/trio/_abc.py',
            f'{SITE}/trio/_core/_run.py',
        ]

    def test_outside_ignored(self, fs):
        """A file installed outside of site-packages is not resolved."""
        dist_info = make_dist(fs, 'demo', ['../../Scripts/demo.py', 'demo/main.py'])
        assert record_sources(SITE, dist_info) == [f'{SITE}/demo/main.py']

    def test_pyc_ignored(self, fs):
        """A bytecode row of a RECORD is never a source."""
        dist_info = make_dist(fs, 'demo', ['demo/main.py', 'demo/__pycache__/main.cpython-38.pyc'])
        assert record_sources(SITE, dist_info) == [f'{SITE}/demo/main.py']

    def test_missing_record(self, fs):
        """A distribution without a RECORD has no source list."""
        fs.create_dir(f'{SITE}/demo-1.0.dist-info')
        assert record_sources(SITE, f'{SITE}/demo-1.0.dist-info') == []

    def test_garbage_record(self, fs):
        """A RECORD that is not UTF-8 has no source list."""
        write(fs, f'{SITE}/demo-1.0.dist-info/RECORD', b'\xff\xfe\x00')
        assert record_sources(SITE, f'{SITE}/demo-1.0.dist-info') == []


class TestPackTier:
    """
    Tests for Scope.pack_tier(): one result per ledger of the pack area.
    """

    def test_index_pack(self, fs):
        """The index pack of a ledger is the file list of its distribution."""
        write(fs, f'{SITE}/.pack/trio/index.pack', TRIO_PACK)
        # The RECORD of the same distribution lists other files: the
        # index pack of the ledger is the authority
        make_dist(fs, 'trio', ['trio/_old.py'])
        results = collect(Scope().pack_tier(SITE, dist_infos(SITE), set()))
        assert len(results) == 1
        assert results[0].tier == TIER_PACK
        assert results[0].target == 'trio'
        assert results[0].sources == [f'{SITE}/trio/_abc.py']
        assert results[0].folder == SITE

    def test_record_fallback(self, fs):
        """A ledger without an index pack falls back to the RECORD."""
        fs.create_dir(f'{SITE}/.pack/trio')
        make_dist(fs, 'trio', ['trio/_abc.py'])
        assert by_target(collect(Scope().pack_tier(SITE, dist_infos(SITE), set()))) == {
            'trio': [f'{SITE}/trio/_abc.py'],
        }

    def test_unresolved(self, fs):
        """A ledger without any file list answers a result without sources."""
        fs.create_dir(f'{SITE}/.pack/gone')
        results = collect(Scope().pack_tier(SITE, dist_infos(SITE), set()))
        assert [(result.target, result.sources) for result in results] == [('gone', [])]

    def test_no_pack_area(self, fs):
        """An environment that no pack flow touched has no pack tier."""
        fs.create_dir(SITE)
        assert collect(Scope().pack_tier(SITE, dist_infos(SITE), set())) == []

    def test_covered_skipped(self, fs):
        """A name an earlier tier covered is not listed, nor marked again."""
        write(fs, f'{SITE}/.pack/trio/index.pack', TRIO_PACK)
        write(fs, f'{SITE}/.pack/attrs/index.pack', TRIO_PACK)
        covered = {'trio'}
        results = collect(Scope().pack_tier(SITE, dist_infos(SITE), covered))
        assert [result.target for result in results] == ['attrs']
        # The names the tier answered are added, so the tiers after it
        # skip them too
        assert covered == {'trio', 'attrs'}

    def test_a_covered_name_without_sources_stays_uncovered(self, fs):
        """A name that could not be listed does not cover the tiers after it."""
        fs.create_dir(f'{SITE}/.pack/gone')
        covered = set()
        collect(Scope().pack_tier(SITE, dist_infos(SITE), covered))
        assert covered == set()

    def test_order(self, fs):
        """The ledgers are answered in the order of their folder name."""
        for name in ('trio', 'attrs', 'zope'):
            fs.create_dir(f'{SITE}/.pack/{name}')
        results = collect(Scope().pack_tier(SITE, dist_infos(SITE), set()))
        assert [result.target for result in results] == ['attrs', 'trio', 'zope']


class TestCommonTier:
    """
    Tests for Scope.common_tier(): one result per name of COMMON_DISTS.
    """

    def test_installed(self, fs):
        """A common name that is installed answers its sources."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        make_dist(fs, 'msgspec', ['msgspec/__init__.py'])
        results = collect(Scope().common_tier(SITE, dist_infos(SITE), set()))
        resolved = [result.target for result in results if result.sources]
        # The order of COMMON_DISTS holds, msgspec is listed before trio
        assert resolved == ['msgspec', 'trio']
        assert by_target(results)['trio'] == [f'{SITE}/trio/_abc.py']

    def test_missing(self, fs):
        """A common name that is not installed answers a result without sources."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        results = collect(Scope().common_tier(SITE, dist_infos(SITE), set()))
        missing = [result.target for result in results if not result.sources]
        # Every other name of COMMON_DISTS, numpy first
        assert missing[:2] == ['numpy', 'scipy']
        assert 'trio' not in missing

    def test_covered_skipped(self, fs):
        """A distribution an earlier tier resolved is not listed again."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        covered = {'trio'}
        results = collect(Scope().common_tier(SITE, dist_infos(SITE), covered))
        assert 'trio' not in [result.target for result in results]
        assert covered == {'trio'}

    def test_one_result_per_name(self, fs):
        """Every name of the list answers exactly one result."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        results = collect(Scope().common_tier(SITE, dist_infos(SITE), set()))
        targets = [result.target for result in results]
        assert len(targets) == len(set(targets))
        assert [result.tier for result in results] == [TIER_COMMON] * len(results)


class TestLockTier:
    """
    Tests for Scope.lock_tier(): one result per pin of a requirements file.
    """

    def test_pins(self, fs):
        """The pins the declaration names give their sources."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        make_dist(fs, 'attrs', ['attr/__init__.py'])
        write(
            fs, '/mod/requirements.txt',
            '# autogenerated\n'
            '--extra-index-url https://example.invalid/simple\n'
            'attrs==23.1.0\n'
            'trio==0.22.0\n'
            'notinstalled==1.0.0\n',
        )
        results = collect(Scope().lock_tier(SITE, dist_infos(SITE), FakeMod('mod', '/mod'), set()))
        assert by_target(results) == {
            'attrs': [f'{SITE}/attr/__init__.py'],
            'trio': [f'{SITE}/trio/_abc.py'],
            'notinstalled': [],
        }
        assert {result.tier for result in results} == {TIER_LOCK}

    def test_covered_skipped(self, fs):
        """A pin an earlier tier resolved is not listed again."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        write(fs, '/mod/requirements.txt', 'trio==0.22.0\n')
        covered = {'trio'}
        assert collect(Scope().lock_tier(SITE, dist_infos(SITE), FakeMod('mod', '/mod'), covered)) == []
        assert covered == {'trio'}

    def test_the_declared_location(self, fs):
        """The requirements file is the one the declaration names."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        write(fs, '/mod/deps/pins.txt', 'trio==0.22.0\n')
        # The default location holds nothing, the declared one does
        mod = FakeMod('mod', '/mod', requirements_file='deps/pins.txt')
        results = collect(Scope().lock_tier(SITE, dist_infos(SITE), mod, set()))
        assert by_target(results) == {'trio': [f'{SITE}/trio/_abc.py']}

    def test_missing_file(self, fs):
        """A mod whose file does not exist answers the file, without sources."""
        fs.create_dir('/mod')
        results = collect(Scope().lock_tier(SITE, dist_infos(SITE), FakeMod('mod', '/mod'), set()))
        assert [(result.target, result.sources) for result in results] == [
            ('/mod/requirements.txt', []),
        ]


class TestModTier:
    """
    Tests for Scope.mod_tier(): one result per mod, read from its ledger.
    """

    def test_index_pack(self, fs):
        """The ledger of the project tree lists the sources of the mod."""
        write(fs, '/mod/.pack/index.pack', MODULE_PACK)
        scope = Scope(site_packages=[], mods={'mod': FakeMod('mod', '/mod')})
        results = collect(scope.mod_tier())
        assert len(results) == 1
        assert results[0].tier == TIER_MOD
        assert results[0].target == 'mod'
        assert sorted(results[0].sources) == ['/mod/module/deep/nested.py', '/mod/module/main.py']
        assert results[0].folder == '/mod'

    def test_without_a_ledger_the_mod_is_skipped(self, fs):
        """A tree without a ledger is never walked."""
        write(fs, '/mod/module/main.py', b'VALUE = 1\n')
        write(fs, '/mod/module/deep/nested.py', b'VALUE = 2\n')
        scope = Scope(site_packages=[], mods={'mod': FakeMod('mod', '/mod')})
        results = collect(scope.mod_tier())
        # The files are on the disk, and the run does not warm them: a
        # walk of a development tree would warm thousands of them
        assert [(result.target, result.sources) for result in results] == [('mod', [])]

    def test_one_result_per_mod(self, fs):
        """Every mod of the loader answers its own result, in its order."""
        for name in ('alas', 'other'):
            write(fs, f'/{name}/.pack/index.pack', MODULE_PACK)
        scope = Scope(site_packages=[], mods={
            'alas': FakeMod('alas', '/alas'),
            'other': FakeMod('other', '/other'),
        })
        results = collect(scope.mod_tier())
        assert [(result.target, result.folder) for result in results] == [
            ('alas', '/alas'),
            ('other', '/other'),
        ]


class TestScopeResult:
    """
    Tests for ScopeResult.
    """

    def test_defaults(self):
        """A result without sources holds its own empty list."""
        first = ScopeResult(TIER_MOD, 'mod')
        second = ScopeResult(TIER_MOD, 'other')
        first.sources.append('/mod/main.py')
        assert second.sources == []
        assert (second.tier, second.target, second.folder) == (TIER_MOD, 'other', '')

    def test_none_sources(self):
        """A unit without a file list answers no source, not None."""
        assert ScopeResult(TIER_MOD, 'mod', None).sources == []


class TestTiers:
    """
    Tests for Scope.tiers(): the whole scope, tier by tier.
    """

    def make(self, fs):
        """
        A site-packages with one distribution per tier and one mod.

        Args:
            fs (FakeFilesystem): The fake filesystem

        Returns:
            Scope: The scope of the tests
        """
        write(fs, f'{SITE}/.pack/trio/index.pack', TRIO_PACK)
        make_dist(fs, 'msgspec', ['msgspec/__init__.py'])
        # attrs is a common name, sympy is only a pin of the file
        make_dist(fs, 'attrs', ['attr/__init__.py'])
        make_dist(fs, 'sympy', ['sympy/__init__.py'])
        write(
            fs, '/mod/requirements.txt',
            'trio==0.22.0\nmsgspec==0.18.0\nattrs==23.1.0\nsympy==1.12\n',
        )
        write(fs, '/mod/.pack/index.pack', MODULE_PACK)
        return Scope(site_packages=[SITE], mods={'mod': FakeMod('mod', '/mod')})

    def test_order_and_dedup(self, fs):
        """The tiers run pack, common, lock, mod, and no name twice."""
        results = collect(self.make(fs).tiers())
        tiers = [result.tier for result in results]
        # A tier is answered in one run of the generator, the caller
        # groups them with itertools.groupby
        assert tiers == sorted(tiers, key=[TIER_PACK, TIER_COMMON, TIER_LOCK, TIER_MOD].index)

        resolved = {
            tier: [result.target for result in results if result.tier == tier and result.sources]
            for tier in (TIER_PACK, TIER_COMMON, TIER_LOCK, TIER_MOD)
        }
        assert resolved[TIER_PACK] == ['trio']
        # attrs is a common name, and msgspec is a pin that is installed
        assert resolved[TIER_COMMON] == ['msgspec', 'attrs']
        # trio, msgspec and attrs are covered by the earlier tiers
        assert resolved[TIER_LOCK] == ['sympy']
        assert resolved[TIER_MOD] == ['mod']

    def test_the_covered_set_is_shared(self, fs):
        """A name is answered by the first tier that resolves it only."""
        results = collect(self.make(fs).tiers())
        resolved = [result.target for result in results if result.sources]
        assert len(resolved) == len(set(resolved))
        assert sorted(resolved) == ['attrs', 'mod', 'msgspec', 'sympy', 'trio']

    def test_every_unit_is_answered(self, fs):
        """A unit the scope could not list answers an empty result."""
        results = collect(self.make(fs).tiers())
        # numpy is in COMMON_DISTS and is not installed
        assert [result.target for result in results if result.tier == TIER_COMMON].count('numpy') == 1
        assert by_target([result for result in results if result.tier == TIER_COMMON])['numpy'] == []


class TestScope:
    """
    Tests for Scope: the folders and the mods of a run.
    """

    def test_injected(self):
        """The folders and the mods of a caller are the ones of the run."""
        mods = {'mod': FakeMod('mod', '/mod')}
        scope = Scope(site_packages=['/site'], mods=mods)
        assert scope.site_packages == ['/site']
        assert scope.mods is mods

    def test_cached(self):
        """The folders are answered once, however often they are read."""
        scope = Scope(site_packages=['/site'])
        assert scope.site_packages is scope.site_packages

    def test_the_environment_of_the_interpreter(self):
        """Without folders of its own, the scope reads the interpreter."""
        assert Scope().site_packages == site_packages_dirs()

    def test_the_mods_of_the_project_root(self, fs, monkeypatch):
        """Without mods of its own, the scope reads the loader of the backend."""
        from alasio.ext import env

        monkeypatch.setattr(env, 'PROJECT_ROOT', '/mod')
        # The loader is built on the project root of the run, so the scope
        # answers what a backend bound to that root answers
        built = []

        class FakeLoader:
            def __init__(self, root):
                built.append(root)
                self.dict_mod = {'mod': FakeMod('mod', '/mod')}

        monkeypatch.setattr('alasio.config.entry.loader.ModLoader', FakeLoader, raising=True)
        scope = Scope(site_packages=[])
        assert list(scope.mods) == ['mod']
        assert built == ['/mod']


class TestSitePackagesDirs:
    """
    Tests for site_packages_dirs(), on the real disk of the interpreter.
    """

    def test_own_environment(self):
        """The folders of the running interpreter are found."""
        dirs = site_packages_dirs()
        assert dirs, 'the interpreter runs without a site-packages folder'
        for path in dirs:
            assert os.path.isdir(path)
            assert os.path.basename(path) in ('site-packages', 'dist-packages')
        assert len(dirs) == len(set(dirs))
