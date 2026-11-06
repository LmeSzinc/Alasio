"""
The client of a dependency that moves from the pip channel to the pack channel.

A mod that does not declare a distribution in entry.pack_deps installs it with
pip: the deploy format keeps no ledger of it, and the version the client is on
is the one of the installation. A later version of the mod declares it
(entry.pack_deps), so the same distribution is served from its packdep channel
afterwards, and the update of that target is incremental -- the packs under
test are the ones the real generator wrote, not hand built ones:

    GET {channel}/2.0/update_1.0.pack      the delta of the version pair
    GET {channel}/2.0/full.pack            range: the index header
    GET {channel}/2.0/full.pack            range: the whole index section

No latest.pack (the version is the snapshot the packed tree records) and no
whole pack download: every record of the delta is applied from the update
pack, and the index pack, which no older ledger can patch, is read from the
front of the full pack.
"""
import os
import py_compile

import pytest
import trio

import alasio.deploy.pack.job as job_module
from alasio.config.entry.mod_entry import MOD_ENTRY_PATH
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job import DeployJob
from alasio.deploy.pack.packdep import decode_packdep
from alasio.deploy.pack.server_file import LatestInfo
from alasio.deploy.simple_pip.pip_install import SimplePip
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel, RepoConfig
from alasio.deploy_dev.pack_server.repo import Repo
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_read_bytes
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy.simple_pip.conftest import build_wheel
from tests.deploy_dev.pack.conftest import MemoryLock, MockServerFile
from tests.deploy_dev.pack_server.conftest import join_path, run_dir  # noqa: F401
from tests.deploy_dev.pack_server.test_fetch_wheel import MockMirror

# The mod of the scenario
MOD = 'DepsMod'
# The index the pins are resolved from: the '# from' annotation of a compiled
# dependency file is the index the pack server fetches the wheel from
PIN_INDEX = 'http://mirror'
# Folder of the workspace in the run directory, and the dist-key of the
# dependency channel (the PEP 503 normalized name)
WORKSPACE = 'pack/Author_Repo_master'
DEP = 'demo'

# t1: the dependency is a wheel of the provided index, installed by pip. t2:
# the same dependency is a pack dep, served from its own channel
ENTRY = {
    't1': (
        'Mod:\n  name: DepsMod\n'
        'Update:\n'
        '  provide_wheel:\n'
        f"  - '{PIN_INDEX}'\n"
    ),
    't2': 'Mod:\n  name: DepsMod\nUpdate:\n  pack_deps:\n  - demo\n',
}

TREE = {
    't1': {
        'module/main.py': b'print("t1")\n',
        'requirements.txt': f'demo==1.0\n    # from {PIN_INDEX}\n'.encode(),
    },
    't2': {
        'module/main.py': b'print("t2")\n',
        'requirements.txt': f'demo==2.0\n    # from {PIN_INDEX}\n'.encode(),
    },
}

CONFIG = PackRepoModel(
    Repo=RepoConfig(
        Remote='https://github.com/Author/Repo',
        Author='Author',
        Repo='Repo',
        Branch='master',
    ),
    Lookback=LookbackConfig(MaxCommitDay=0),
)


def build_repo(*commits):
    """
    Build a mock repo of the scenario, its head is the last commit.

    Args:
        *commits (str): Commits to register, the parent of a commit is the one
            before it

    Returns:
        MockGitRepo: The repo, its branch master points at the last commit
    """
    repo = MockGitRepo()
    parent = None
    for index, commit in enumerate(commits):
        for path, content in TREE[commit].items():
            repo.register_file(commit, path, content)
        repo.register_file(commit, MOD_ENTRY_PATH, ENTRY[commit].encode())
        repo.register_commit(
            commit, parents=[parent] if parent else None,
            author_name='Author', author_time=index, message=f'commit {commit}')
        parent = commit
    repo.register_branch('master', parent)
    repo.register_head(parent)
    return repo


def register_wheels(mirror):
    """
    Register the wheels of the dependency in the mirror of the run.

    Args:
        mirror (MockMirror): The pypi mirror the pack server fetches from
    """
    for version in ('1.0', '2.0'):
        path = f'/w/{DEP}-{version}-py3-none-any.whl'
        build_wheel(
            path,
            {
                f'{DEP}/__init__.py': b'',
                f'{DEP}/core.py': f"VERSION = '{version}'\n".encode(),
            },
            name=DEP, version=version,
        )
        mirror.register(DEP, version, content=file_read_bytes(path))


def generate(repo, mirror):
    """
    Run the pack server over a repo: the wheels, the packs and the publication.

    Args:
        repo (MockGitRepo): The repo to pack
        mirror (MockMirror): The pypi mirror the wheels are fetched from

    Returns:
        Repo: The flow that ran
    """
    gen = Repo(repo, CONFIG, client=mirror.client)
    gen.run()
    gen.commit()
    return gen


def channel_files(folder):
    """
    The files of a generated channel, keyed by their path in the channel.

    Args:
        folder (PathStr): Folder of the channel in the run directory

    Returns:
        dict[str, bytes]: {path relative to the channel: content}
    """
    prefix = str(folder).rstrip('/\\')
    return {
        str(path)[len(prefix):].replace('\\', '/').lstrip('/'): path.atomic_read_bytes()
        for path in folder.iter_files(recursive=True)
    }


def snapshot_of(root, commit):
    """
    The dependency snapshot the packed tree of a commit records.

    Args:
        root (PathStr): Workspace of the run
        commit (str): Commit of the tree

    Returns:
        dict[str, LatestInfo]: {dist-key: version and index checksum}
    """
    pack = PathStr.new(join_path(root, 'packrepo', commit, 'full.pack')).atomic_read_bytes()
    tree = PackDecodeBase(pack)
    record = tree.fileinfo['.pack/packdep.pack']
    decoded = decode_packdep(bytes(tree.catfile(record)))
    items = decoded.values() if isinstance(decoded, dict) else decoded
    return {
        item.name: LatestInfo(item.version, bytes(item.index_checksum).hex())
        for item in items
    }


@pytest.mark.trio
async def test_the_update_takes_the_delta_and_the_index_section(fs, run_dir, monkeypatch):
    """
    The version of the installation is the version the update pack is asked
    for, and the flow reads the packs of the channel and nothing else.
    """
    # the pyc of a pip install is written into the fake filesystem, which the
    # real compiler cannot do; SQLite opens its file in the C library, which
    # cannot see it either
    monkeypatch.setattr(py_compile, 'compile', lambda *a, **k: None)
    monkeypatch.setattr(job_module, 'SQLiteFileLock', MemoryLock)

    mirror = MockMirror()
    register_wheels(mirror)
    project = PathStr.new(run_dir)

    # the state the previous process left: the dependency is a pip
    # installation of the wheel of the provided index, with no ledger
    await trio.to_thread.run_sync(generate, build_repo('t1'), mirror)
    root = project.joinpath(WORKSPACE)
    wheels = [
        str(path)
        for path in PathStr.new(join_path(root, 'wheel', DEP, '1.0')).iter_files(recursive=True)
        if str(path).endswith('.whl')
    ]
    assert wheels, 'the run did not publish the wheel of the dependency'
    site = PathStr.new('/site')
    fs.create_dir(site)
    SimplePip(site).install(wheels[0])
    assert not os.path.exists(PathStr.new(join_path(site, '.pack', DEP, 'index.pack')))

    # the next run of the pack server publishes the version that declares the
    # dependency a pack dep
    await trio.to_thread.run_sync(generate, build_repo('t1', 't2'), mirror)
    channel = channel_files(PathStr.new(join_path(root, 'packdep', DEP)))
    # the channel of a dependency carries the target version only, and the
    # client has the folder of t1 already
    assert sorted(channel) == ['2.0/full.pack', '2.0/update_1.0.pack']

    # serve the channel exactly as the generator wrote it
    server = MockServerFile(
        f'https://pack.example.com/alas/LmeSzinc_Alasio_master/packdep/{DEP}')
    full = channel['2.0/full.pack']
    server.register_version('2.0', full, bytes(PackDecodeBase(full).extract_index_pack()))
    server.register_update('1.0', '2.0', channel['2.0/update_1.0.pack'])

    # the version source of the dependency is the snapshot of the tree, so
    # latest.pack is never requested
    snapshot = snapshot_of(root, 't2')
    assert snapshot[DEP].version == '2.0'

    job = DeployJob(root=site, name=DEP, server=server, scene='pypi')
    assert await job.update(version='2.0', latest_info=snapshot[DEP])

    # two urls: the delta of the version pair, and two ranges of the index
    # section of the full pack (the header and the whole section). Nothing
    # else was requested, the whole pack was never downloaded and no record
    # of the delta had to be fetched from it
    assert server.requests == [
        f'https://pack.example.com/alas/LmeSzinc_Alasio_master/packdep/{DEP}/2.0/update_1.0.pack',
        f'https://pack.example.com/alas/LmeSzinc_Alasio_master/packdep/{DEP}/2.0/full.pack',
        f'https://pack.example.com/alas/LmeSzinc_Alasio_master/packdep/{DEP}/2.0/full.pack',
    ]
    assert server.full_bytes == 0

    # the tree of the new version, and the ledger the flow committed
    target = join_path(site, 'demo-2.0.dist-info', 'METADATA')
    assert b'Version: 2.0' in file_read_bytes(target)
    assert not os.path.exists(PathStr.new(join_path(site, 'demo-1.0.dist-info')))
    assert file_read_bytes(
        join_path(site, '.pack', DEP, 'index.pack')
    ) == bytes(PackDecodeBase(full).extract_index_pack())
