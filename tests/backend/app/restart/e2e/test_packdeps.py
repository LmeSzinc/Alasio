"""
Scenario four of the realcase runs: the python dependencies a mod updates by pack

The real ``FlowStartup`` over the real backend of the conftest, with the real
``InstallBase``: the requirements file of a mod belongs to its tree and pins
the distributions it runs with, the ones the entry declares for pack updates
draw from the pack channel of the mod (``packdep/{dep}``, a sibling of the
``packrepo`` channel of its tree). The full update of the startup reconciles
the dependencies of every managed mod after their trees: a pin the environment
does not satisfy is installed, a pin it satisfies is verified and repaired --
a dependency that has no update is validated all the same. The second
dependency of the scenario keeps its version across the tree update and is
validated in place.

The channels are the output of the pack server itself: the ``Repo`` flow packs
the mock git repo of the scenario, the wheels of the distributions come from
the mock pypi mirror of the fetch tests, and the workspace it writes is
published with the names and the folder layout the generator wrote. No pack
and no dependency snapshot is built by the test, so a file the client reads
that the generator does not write is a 404 of the mock backend.

The environment the dependencies land in is the site-packages of the process,
which a test must not touch: ``local_site_packages`` of the step layer is
patched to a folder of the in-memory filesystem, everything else is the real
code (the real channels, the real deploy job of the install).
"""
import pytest
import trio

from alasio.backend.app.restart import restart_step as step_module
from alasio.config.entry.mod_entry import MOD_ENTRY_PATH
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy_dev.pack_server.model import LookbackConfig, PackRepoModel, RepoConfig
from alasio.deploy_dev.pack_server.repo import Repo
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_read_bytes, file_write
from alasio.git.mock.mock_repo import MockGitRepo
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy.simple_pip.conftest import build_wheel, list_folders, site_packages
from tests.deploy_dev.pack_server.conftest import join_path
from tests.deploy_dev.pack_server.test_fetch_wheel import MockMirror

from .conftest import GENERATED_HOST, assert_logs, at

# The mod of the scenario: the repo the pack server packs is its tree
MOD = 'DepsMod'

# The index the pins of the dependency file are resolved from: the mock pypi
# mirror of the run. The '# from' annotation is the one a compiled dependency
# file carries, and it is the index the pack server fetches the wheel from
PIN_INDEX = 'http://mirror'


def requirement(pin):
    """
    A pin line and the '# from' annotation of a compiled dependency file.

    Args:
        pin (str): Pin as written in the file, e.g. 'demo==1.0'

    Returns:
        str: The pin and its annotation, each on a line of its own
    """
    return f'{pin}\n    # from {PIN_INDEX}\n'


# The trees of the scenario: the requirements file is part of the tree, so an
# update of the tree brings the pins of its dependencies. t1 pins demo 1.0,
# t2 pins demo 2.0; stable stays at 1.0 in both versions.
TREE = {
    't1': {
        'module/main.py': b'print("t1")\n',
        'requirements.txt': (requirement('demo==1.0') + requirement('stable==1.0')).encode(),
    },
    't2': {
        'module/main.py': b'print("t2")\n',
        'requirements.txt': (requirement('demo==2.0') + requirement('stable==1.0')).encode(),
    },
}
# The mod entry of the tree: the pack server builds the channel of every name
# the entry declares pack_deps of, from the pins of the requirements file above
ENTRY = 'Mod:\n  name: DepsMod\nUpdate:\n  pack_deps:\n  - demo\n  - stable\n'

# The config of the pack server run: the window is unlimited, the dependency
# file and the pack names are declared by the mod entry of the repo
CONFIG = PackRepoModel(
    Repo=RepoConfig(
        Remote='https://github.com/Author/Repo',
        Author='Author',
        Repo='Repo',
        Branch='master',
    ),
    Lookback=LookbackConfig(MaxCommitDay=0),
)
# Folder of the workspace in the run directory, and the mirror of the mod: the
# packrepo channel of the generated workspace, the packdep channels of its
# dependencies are its siblings
WORKSPACE = 'pack/Author_Repo_master'
MIRROR = f'http://{GENERATED_HOST}/{WORKSPACE}/packrepo'
# The dist-keys of the dependency channels (the PEP 503 normalized names)
DEP = 'demo'
STABLE = 'stable'

# The content of the builtin .gitattributes, read before the fake filesystem
# is active: the packs are built inside the tests, where the pack encoder reads
# it from the fake filesystem like the real one
GITATTRIBUTES = file_read_bytes(env.ALASIO_ROOT.joinpath('.gitattributes'))


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
        repo.register_file(commit, MOD_ENTRY_PATH, ENTRY.encode())
        repo.register_commit(
            commit, parents=[parent] if parent else None,
            author_name='Author', author_time=index, message=f'commit {commit}')
        parent = commit
    repo.register_branch('master', parent)
    repo.register_head(parent)
    return repo


def register_wheels(mirror):
    """
    Register the wheels of the distributions of the scenario in the mirror.

    Args:
        mirror (MockMirror): The pypi mirror the pack server fetches from
    """
    for name, version in ((DEP, '1.0'), (DEP, '2.0'), (STABLE, '1.0')):
        path = f'/w/{name}-{version}-py3-none-any.whl'
        build_wheel(
            path,
            {
                f'{name}/__init__.py': b'',
                f'{name}/core.py': f"VERSION = '{version}'\n".encode(),
            },
            name=name, version=version,
        )
        mirror.register(name, version, content=file_read_bytes(path))


def generate(repo, mirror):
    """
    Run the pack server over a repo: the wheels, the packs and the publication.

    The steps that fetch run on an event loop of their own (``RepoDep``), so
    the flow runs on a worker thread of the test, away from the loop of the
    backend.

    Args:
        repo (MockGitRepo): The repo to pack
        mirror (MockMirror): The pypi mirror the wheels are fetched from

    Returns:
        Repo: The flow that ran, its steps carry what was generated
    """
    gen = Repo(repo, CONFIG, client=mirror.client)
    gen.run()
    gen.commit()
    return gen


def publish(server, project):
    """
    Publish the generated workspace, the files exactly as they are on the disk.

    A deployment serves the two channels of the workspace, packrepo and
    packdep; the private wheel cache is not part of it. Nothing is renamed on
    the way to the client, so a channel the generator does not write is a 404.

    Args:
        server (MockUpdateServer): The mock server of the generated host
        project (Project): Project root of the run
    """
    root = project.root.joinpath(WORKSPACE)
    for folder in ('packrepo', 'packdep'):
        server.register_folder(f'/{WORKSPACE}/{folder}', join_path(root, folder))


async def install_previous(project, site, commit, pins):
    """
    Install the state a previous process left: the tree and its dependencies.

    Both are unpacked from the packs the generator wrote, so the state the
    update starts from is the output of the pack server too.

    Args:
        project (Project): Project root of the run
        site (PathStr): Site-packages folder of the in-memory environment
        commit (str): Commit of the tree to install
        pins (dict[str, str]): {distribution: version} to install
    """
    root = project.root.joinpath(WORKSPACE)
    await project.install(MOD, pack=file_read_bytes(join_path(root, 'packrepo', commit, 'full.pack')))
    for name, version in pins.items():
        await UnpackJob(
            file_read_bytes(join_path(root, 'packdep', name, version, 'full.pack')),
            root=site, name=name,
        ).run()


@pytest.mark.trio
async def test_a_tree_update_brings_the_dependencies_of_its_new_requirements(
        fs, project, servers, make_backend, monkeypatch):
    """
    A cold start finds an update of the mod: the tree is applied and its
    dependencies are reconciled after it -- the updated pin is installed from
    its packdep channel, the unchanged pin is validated in place, then the
    flow hands the process over (the new packages are imported by the new
    process)
    """
    backend = make_backend(
        mods={MOD: MIRROR}, configs={}, pack_deps={MOD: [DEP, STABLE]})
    site = PathStr.new(site_packages(fs))
    monkeypatch.setattr(step_module, 'local_site_packages', lambda: site)
    mod_root = project.mod_root(MOD)
    # the fake filesystem starts without the builtin attributes, the pack
    # encoder reads them from env.ALASIO_ROOT like a real run
    fs.create_file(env.ALASIO_ROOT.joinpath('.gitattributes'), contents=GITATTRIBUTES)
    mirror = MockMirror()
    register_wheels(mirror)
    # the run that published the version the previous process runs: its tree
    # and its dependencies are installed from the packs of that run
    await trio.to_thread.run_sync(generate, build_repo('t1'), mirror)
    await install_previous(project, site, 't1', {DEP: '1.0', STABLE: '1.0'})
    # the next run of the pack server publishes t2: the tree update pack, the
    # update pack of the dependency that moved, and the snapshot the tree
    # carries for the client
    await trio.to_thread.run_sync(generate, build_repo('t1', 't2'), mirror)
    publish(servers.servers[GENERATED_HOST], project)
    # the channel the client reads is exactly what the generator wrote: the
    # packs of the version it publishes, and none of the version before it
    # (the cleanup of the run removed the folder of t1, the client has it)
    files = servers.servers[GENERATED_HOST].static.files
    assert f'/{WORKSPACE}/packrepo/latest.pack' in files
    assert f'/{WORKSPACE}/packrepo/t2/update_t1.pack' in files
    assert f'/{WORKSPACE}/packrepo/t1/full.pack' not in files
    # a dependency channel carries the version folder of its target only: the
    # version a client installs is the packdep snapshot of the tree, never a
    # per dependency latest.pack, see RepoDep
    assert f'/{WORKSPACE}/packdep/{DEP}/2.0/update_1.0.pack' in files
    assert not [
        path for path in files
        if path.startswith(f'/{WORKSPACE}/packdep/') and path.endswith('/latest.pack')
    ]

    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Update] Updating mod "DepsMod"
INFO | [CurrentVersion] t1
INFO | [LatestVersion] t2
INFO | Update available: t1 -> t2
INFO | Updating files to "{mod_root}"
INFO | Replacing files to "{mod_root}"
INFO | Update done
INFO | [Update] Applied the update of "DepsMod"
INFO | [Update] Updating the dependencies of "DepsMod"
INFO | Installing "demo==2.0" to "{site}"
INFO | [CurrentVersion] 1.0
INFO | [LatestVersion] 2.0
INFO | Update available: 1.0 -> 2.0
INFO | Updating files to "{site}", name="demo"
INFO | Replacing files to "{site}", name="demo"
INFO | Update done
INFO | Installed "demo==2.0"
INFO | Installing "stable==1.0" to "{site}"
INFO | [CurrentVersion] 1.0
INFO | [LatestVersion] 1.0
INFO | Already up to date: 1.0
INFO | Resetting files to "{site}", name="stable"
INFO | Reset done
INFO | Installed "stable==1.0"
INFO | [Update] The dependencies of "DepsMod" are at their pinned versions
INFO | [Update] Full update finished, changed mods: ['DepsMod'], failed mods: []
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 0 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "startup" flow (startup convergence): 0 configs to resume
INFO | [Restart] Flow "startup" finished
""")
            nursery.cancel_scope.cancel()
    # the updated dependency landed in the environment, and no metadata
    # directory of its old version stays next to it
    assert file_read_bytes(f'{site}/{DEP}/core.py') == b"VERSION = '2.0'\n"
    folders = list_folders(site)
    assert [folder for folder in folders if folder.startswith(f'{DEP}-')] == [f'{DEP}-2.0.dist-info']
    # the second dependency kept its version and was validated in place
    assert file_read_bytes(f'{site}/{STABLE}/core.py') == b"VERSION = '1.0'\n"
    assert [folder for folder in folders if folder.startswith(f'{STABLE}-')] == [f'{STABLE}-1.0.dist-info']


@pytest.mark.trio
async def test_the_startup_check_verifies_a_dependency_without_an_update(
        fs, project, servers, make_backend, monkeypatch):
    """
    A cold start with nothing to apply: the tree is validated and both
    dependencies -- neither has an update -- are verified by their channels
    all the same, a damaged file is downloaded again. The flows of a mod
    update validate the whole environment, not only what moved
    """
    backend = make_backend(
        mods={MOD: MIRROR}, configs={}, pack_deps={MOD: [DEP, STABLE]})
    site = PathStr.new(site_packages(fs))
    monkeypatch.setattr(step_module, 'local_site_packages', lambda: site)
    mod_root = project.mod_root(MOD)
    fs.create_file(env.ALASIO_ROOT.joinpath('.gitattributes'), contents=GITATTRIBUTES)
    mirror = MockMirror()
    register_wheels(mirror)
    # the channel of the version the process runs, generated by the pack
    # server like the one of an update
    await trio.to_thread.run_sync(generate, build_repo('t1'), mirror)
    await install_previous(project, site, 't1', {DEP: '1.0', STABLE: '1.0'})
    # the file of the dependency is damaged after the install of the previous
    # process (nothing announces it: only the validation finds it)
    file_write(site.joinpath(f'{DEP}/core.py'), b'damaged\n')
    publish(servers.servers[GENERATED_HOST], project)

    with logger.mock_capture_writer() as capture:
        async with trio.open_nursery() as nursery:
            await backend.start(nursery)
            assert_logs(capture, f"""\
INFO | [Restart] Flow "init" accepted
INFO | [Restart] Flow "startup" accepted
INFO | [Update] Updating mod "DepsMod"
INFO | [CurrentVersion] t1
INFO | [LatestVersion] t1
INFO | Already up to date: t1
INFO | Resetting files to "{mod_root}"
INFO | Reset done
INFO | [Update] Updating the dependencies of "DepsMod"
INFO | Installing "demo==1.0" to "{site}"
INFO | [CurrentVersion] 1.0
INFO | [LatestVersion] 1.0
INFO | Already up to date: 1.0
INFO | Resetting files to "{site}", name="demo"
INFO | Reset done
INFO | Installed "demo==1.0"
INFO | Installing "stable==1.0" to "{site}"
INFO | [CurrentVersion] 1.0
INFO | [LatestVersion] 1.0
INFO | Already up to date: 1.0
INFO | Resetting files to "{site}", name="stable"
INFO | Reset done
INFO | Installed "stable==1.0"
INFO | [Update] The dependencies of "DepsMod" are at their pinned versions
INFO | [Update] Full update finished, changed mods: [], failed mods: []
INFO | [Restart] Flow "startup" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
            nursery.cancel_scope.cancel()
    # the damaged file was downloaded again from the channel
    assert file_read_bytes(f'{site}/{DEP}/core.py') == b"VERSION = '1.0'\n"
    # the unchanged dependency was validated and left at its version
    assert file_read_bytes(f'{site}/{STABLE}/core.py') == b"VERSION = '1.0'\n"
