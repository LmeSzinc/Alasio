"""
Tests of the scope of a warmup, see alasio/deploy/pyc_warm/scope.py.
"""
import os

import pytest

from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_base import DeployTarget
from alasio.deploy.pyc_warm import scope as scope_module
from alasio.deploy.pyc_warm.scope import (
    ScopeResult, common_tier, dist_infos, index_sources, localize_path, lock_tier, mod_sources, pack_tier,
    record_sources, site_packages_dirs, site_packages_tiers, walk_sources
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
    Tests for pack_tier().
    """

    def test_index_pack(self, fs):
        """The index pack of a ledger is the file list of its distribution."""
        write(fs, f'{SITE}/.pack/trio/index.pack', TRIO_PACK)
        # The RECORD of the same distribution lists other files: the
        # index pack of the ledger is the authority
        make_dist(fs, 'trio', ['trio/_old.py'])
        result = pack_tier(SITE, dist_infos(SITE))
        assert result.sources == [f'{SITE}/trio/_abc.py']
        assert result.resolved == ['trio']
        assert result.unresolved == []

    def test_record_fallback(self, fs):
        """A ledger without an index pack falls back to the RECORD."""
        fs.create_dir(f'{SITE}/.pack/trio')
        make_dist(fs, 'trio', ['trio/_abc.py'])
        result = pack_tier(SITE, dist_infos(SITE))
        assert result.sources == [f'{SITE}/trio/_abc.py']
        assert result.resolved == ['trio']

    def test_unresolved(self, fs):
        """A ledger without any file list is reported, not fatal."""
        fs.create_dir(f'{SITE}/.pack/gone')
        result = pack_tier(SITE, dist_infos(SITE))
        assert result.sources == []
        assert result.unresolved == ['gone']

    def test_no_pack_area(self, fs):
        """An environment that no pack flow touched has no pack tier."""
        fs.create_dir(SITE)
        result = pack_tier(SITE, dist_infos(SITE))
        assert (result.sources, result.resolved, result.unresolved) == ([], [], [])


class TestCommonTier:
    """
    Tests for common_tier().
    """

    def test_resolved(self, fs):
        """The common names that are installed give their sources."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        make_dist(fs, 'msgspec', ['msgspec/__init__.py'])
        result = common_tier(SITE, dist_infos(SITE))
        # The order of COMMON_DISTS holds, msgspec is listed before trio
        assert result.sources == [f'{SITE}/msgspec/__init__.py', f'{SITE}/trio/_abc.py']
        assert result.resolved == ['msgspec', 'trio']
        # Every other name of COMMON_DISTS is not installed, numpy first
        assert result.unresolved[:2] == ['numpy', 'scipy']
        assert 'trio' not in result.unresolved

    def test_covered_skipped(self, fs):
        """A distribution an earlier tier resolved is not listed again."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        result = common_tier(SITE, dist_infos(SITE), {'trio'})
        assert result.sources == []
        assert 'trio' not in result.resolved
        assert 'trio' not in result.unresolved


class TestLockTier:
    """
    Tests for lock_tier().
    """

    def test_pins(self, fs):
        """The pins of the requirements file give their sources."""
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
        result = lock_tier(SITE, dist_infos(SITE), '/mod/requirements.txt')
        assert result.sources == [f'{SITE}/attr/__init__.py', f'{SITE}/trio/_abc.py']
        assert result.resolved == ['attrs', 'trio']
        assert result.unresolved == ['notinstalled']

    def test_covered_skipped(self, fs):
        """A pin an earlier tier resolved is not listed again."""
        make_dist(fs, 'trio', ['trio/_abc.py'])
        write(fs, '/mod/requirements.txt', 'trio==0.22.0\n')
        result = lock_tier(SITE, dist_infos(SITE), '/mod/requirements.txt', {'trio'})
        assert (result.sources, result.resolved, result.unresolved) == ([], [], [])

    def test_missing_file(self, fs):
        """A tree without a requirements file has no lock tier."""
        fs.create_dir('/mod')
        result = lock_tier(SITE, dist_infos(SITE), '/mod/requirements.txt')
        assert (result.sources, result.resolved, result.unresolved) == ([], [], [])


class TestSitePackagesTiers:
    """
    Tests for site_packages_tiers().
    """

    def test_order_and_dedup(self, fs):
        """The tiers run pack, common, lock, and no distribution twice."""
        write(fs, f'{SITE}/.pack/trio/index.pack', TRIO_PACK)
        make_dist(fs, 'msgspec', ['msgspec/__init__.py'])
        # attrs is a common name, sympy is only a pin of the file
        make_dist(fs, 'attrs', ['attr/__init__.py'])
        make_dist(fs, 'sympy', ['sympy/__init__.py'])
        write(
            fs, '/mod/requirements.txt',
            'trio==0.22.0\nmsgspec==0.18.0\nattrs==23.1.0\nsympy==1.12\n',
        )

        tiers = site_packages_tiers(SITE, '/mod/requirements.txt')
        assert [name for name, _ in tiers] == ['pack', 'common', 'lock']
        pack, common, lock = (scope for _, scope in tiers)
        assert pack.sources == [f'{SITE}/trio/_abc.py']
        assert common.sources == [f'{SITE}/msgspec/__init__.py', f'{SITE}/attr/__init__.py']
        # trio, msgspec and attrs are covered by the earlier tiers
        assert lock.sources == [f'{SITE}/sympy/__init__.py']

    def test_no_requirements_file(self, fs):
        """Without a requirements file the lock tier is empty."""
        fs.create_dir(SITE)
        tiers = site_packages_tiers(SITE)
        assert [name for name, _ in tiers] == ['pack', 'common', 'lock']
        assert tiers[2][1].sources == []


class TestModSources:
    """
    Tests for mod_sources().
    """

    def test_index_pack(self, fs):
        """The ledger of the project tree lists the sources of the mod."""
        write(fs, '/mod/.pack/index.pack', MODULE_PACK)
        assert sorted(mod_sources('/mod')) == ['/mod/module/deep/nested.py', '/mod/module/main.py']

    def test_walk_fallback(self, fs):
        """A tree without a ledger is read from the disk."""
        write(fs, '/mod/module/main.py', b'VALUE = 1\n')
        write(fs, '/mod/module/deep/nested.py', b'VALUE = 2\n')
        write(fs, '/mod/module/data.json', b'{}\n')
        assert sorted(mod_sources('/mod')) == ['/mod/module/deep/nested.py', '/mod/module/main.py']


class TestWalkSources:
    """
    Tests for walk_sources().
    """

    def test_skip(self, fs):
        """The frozen folders of the tree are never entered."""
        write(fs, '/mod/module/main.py', b'')
        for folder in ('__pycache__', '.git', '.pack', 'node_modules', 'site-packages', 'dist-packages'):
            write(fs, f'/mod/{folder}/ignored.py', b'')
        assert walk_sources('/mod') == ['/mod/module/main.py']

    def test_missing_folder(self, fs):
        """A folder that does not exist holds no source."""
        assert walk_sources('/missing') == []

    def test_environment_skipped(self, fs, monkeypatch):
        """The environment of the interpreter is never walked."""
        write(fs, '/mod/module/main.py', b'')
        write(fs, '/mod/toolkit/Lib/os.py', b'')
        write(fs, '/mod/toolkit/Lib/site-packages/trio/_abc.py', b'')
        monkeypatch.setattr(scope_module, 'environment_dirs', lambda: frozenset({'/mod/toolkit'}))
        assert walk_sources('/mod') == ['/mod/module/main.py']


class TestScopeResult:
    """
    Tests for ScopeResult.
    """

    def test_defaults(self):
        """An empty scope has three empty lists, not shared ones."""
        first = ScopeResult()
        second = ScopeResult()
        first.sources.append('/mod/main.py')
        assert (second.sources, second.resolved, second.unresolved) == ([], [], [])


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
