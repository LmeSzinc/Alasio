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

The environment the dependencies land in is the site-packages of the process,
which a test must not touch: ``local_site_packages`` of the step layer is
patched to a folder of the in-memory filesystem, everything else is the real
code (the real channels, the real deploy job of the install).
"""
from types import SimpleNamespace

import pytest
import trio

from alasio.backend.app.restart import restart_step as step_module
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.pack.pack_wheel_update import PackWheelUpdate
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_read_bytes, file_write
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy.simple_pip.conftest import build_wheel, list_folders, site_packages
from tests.deploy_dev.pack.conftest import make_repo

from .conftest import UPDATE_HOST, assert_logs, at

# The tree of the scenario: its requirements file is part of it, so an update
# of the tree brings the pins of its dependencies. t1 pins demo 1.0, t2 pins
# demo 2.0; stable stays at 1.0 in both versions.
REPO = make_repo({
    't1': {
        'module/main.py': b'print("t1")\n',
        'requirements.txt': b'demo==1.0\nstable==1.0\n',
    },
    't2': {
        'module/main.py': b'print("t2")\n',
        'requirements.txt': b'demo==2.0\nstable==1.0\n',
    },
})
# The content of the builtin .gitattributes, read before the fake filesystem
# is active: the tree packs are built inside the tests, where the pack
# encoder reads it from the fake filesystem like the real one
GITATTRIBUTES = file_read_bytes(env.ALASIO_ROOT.joinpath('.gitattributes'))

# The path prefix of the deployment on the mock host, and the mirror of the
# mod: the packrepo folder of its tree channel, the packdep folders of its
# dependencies are derived from it
CHANNEL = '/alas/LmeSzinc_Alasio_master'
MIRROR = f'http://{UPDATE_HOST}{CHANNEL}/packrepo'
# The dist-keys of the dependency channels (the PEP 503 normalized names)
DEP = 'demo'
STABLE = 'stable'


def publish(server, prefix, version, full, index):
    """
    Publish a version of a channel (the layout of a pack deployment).

    Args:
        server (MockUpdateServer): The mock server of the host
        prefix (str): Path prefix of the channel below the host, e.g.
            '/alas/LmeSzinc_Alasio_master/packrepo'
        version (str): Version to publish, it becomes the latest one
        full (bytes): Full pack of the version
        index (bytes): Index pack of the version
    """
    checksum = bytes.fromhex(PackDecodeBase(index).index_checksum)
    static = server.static
    static.register_file(f'{prefix}/latest.pack', version.encode() + checksum)
    static.register_file(f'{prefix}/{version}/full.pack', full)


def publish_update(server, prefix, old, new, update):
    """
    Publish the update pack of a version pair of a channel.

    Args:
        server (MockUpdateServer): The mock server of the host
        prefix (str): Path prefix of the channel below the host
        old (str): Version the update applies to
        new (str): Version the update installs
        update (bytes): Update pack of the pair
    """
    server.static.register_file(f'{prefix}/{new}/update_{old}.pack', update)


def dep_wheel(version, name=DEP):
    """
    Build the wheel of a dependency at a version (on the fake filesystem).

    Args:
        version (str): Version of the dependency
        name (str): Distribution name. Defaults to DEP

    Returns:
        str: Path of the .whl file
    """
    return build_wheel(
        f'/w/{name}-{version}-py3-none-any.whl',
        {
            f'{name}/__init__.py': b'',
            f'{name}/core.py': f"VERSION = '{version}'\n".encode(),
        },
        name=name,
        version=version,
    )


def dep_pack(version, name=DEP):
    """
    The pack data of a dependency at a version.

    Args:
        version (str): Version of the dependency
        name (str): Distribution name. Defaults to DEP

    Returns:
        tuple[bytes, bytes]: The full pack and the index pack
    """
    pack = PackWheel(dep_wheel(version, name))
    return b''.join(pack.iter_pack_data()), bytes(pack.index_pack)


def tree_pack(commit, snapshots=None):
    """
    The tree packs of a version and their packdep snapshots.

    Args:
        commit (str): Commit to pack
        snapshots (dict[str, tuple[str, bytes]], optional): {name: (version,
            20 bytes index checksum)} recorded in .pack/packdep.pack.
            Defaults to None, a tree without packdep

    Returns:
        tuple[bytes, bytes, PackFull]: Full pack data, index pack data and
            the PackFull that builds them (the new side of an update pack)
    """
    dep_gen = None
    if snapshots is not None:
        dep_gen = SimpleNamespace(published=snapshots)
    pack = PackFull(REPO, commit=commit, dep_gen=dep_gen)
    return b''.join(pack.iter_pack_data()), b''.join(pack.iter_packidx_data()), pack


def dep_snapshots(demo_version, demo_index, stable_index):
    """
    The packdep snapshots of a tree version.

    Args:
        demo_version (str): Version of demo the tree pins
        demo_index (bytes): Index pack of that demo version
        stable_index (bytes): Index pack of stable 1.0

    Returns:
        dict[str, tuple[str, bytes]]: Snapshot of every dependency
    """
    return {
        DEP: (demo_version, bytes.fromhex(PackDecodeBase(demo_index).index_checksum)),
        STABLE: ('1.0', bytes.fromhex(PackDecodeBase(stable_index).index_checksum)),
    }


def dep_update(old, new, name=DEP):
    """
    The update pack of a version pair of a dependency.

    Args:
        old (str): Version the update applies to
        new (str): Version the update installs
        name (str): Distribution name. Defaults to DEP

    Returns:
        bytes: Update pack of the pair
    """
    return b''.join(PackWheelUpdate(
        PackWheel(dep_wheel(new, name)), PackWheel(dep_wheel(old, name))).iter_pack_data())


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
        mods={'DepsMod': MIRROR}, configs={}, pack_deps={'DepsMod': [DEP, STABLE]})
    site = PathStr.new(site_packages(fs))
    monkeypatch.setattr(step_module, 'local_site_packages', lambda: site)
    mod_root = project.mod_root('DepsMod')
    # the tree and the dependencies of the process this one replaces; the
    # tree predates packdep, the update brings the snapshot
    fs.create_file(env.ALASIO_ROOT.joinpath('.gitattributes'), contents=GITATTRIBUTES)
    full_t1, _, _ = tree_pack('t1')
    await project.install('DepsMod', pack=full_t1)
    dep_1_full, dep_1_index = dep_pack('1.0')
    await UnpackJob(dep_1_full, root=site, name=DEP).run()
    stable_full, stable_index = dep_pack('1.0', STABLE)
    await UnpackJob(stable_full, root=site, name=STABLE).run()
    dep_2_full, dep_2_index = dep_pack('2.0')
    full_t2, index_t2, pack_t2 = tree_pack(
        't2', dep_snapshots('2.0', dep_2_index, stable_index))
    update_t1_t2 = b''.join(PackUpdate(pack_t2, 't1').iter_pack_data())
    server = servers.servers[UPDATE_HOST]
    publish(server, f'{CHANNEL}/packrepo', 't2', full_t2, index_t2)
    publish_update(server, f'{CHANNEL}/packrepo', 't1', 't2', update_t1_t2)
    publish(server, f'{CHANNEL}/packdep/{DEP}', '2.0', dep_2_full, dep_2_index)
    publish_update(server, f'{CHANNEL}/packdep/{DEP}', '1.0', '2.0', dep_update('1.0', '2.0'))
    publish(server, f'{CHANNEL}/packdep/{STABLE}', '1.0', stable_full, stable_index)

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
INFO | [Update] Full update finished, changed mods: ['DepsMod']
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
        mods={'DepsMod': MIRROR}, configs={}, pack_deps={'DepsMod': [DEP, STABLE]})
    site = PathStr.new(site_packages(fs))
    monkeypatch.setattr(step_module, 'local_site_packages', lambda: site)
    mod_root = project.mod_root('DepsMod')
    dep_1_full, dep_1_index = dep_pack('1.0')
    stable_full, stable_index = dep_pack('1.0', STABLE)
    fs.create_file(env.ALASIO_ROOT.joinpath('.gitattributes'), contents=GITATTRIBUTES)
    full_t1, index_t1, _ = tree_pack(
        't1', dep_snapshots('1.0', dep_1_index, stable_index))
    await project.install('DepsMod', pack=full_t1)
    await UnpackJob(dep_1_full, root=site, name=DEP).run()
    await UnpackJob(stable_full, root=site, name=STABLE).run()
    # the file of the dependency is damaged after the install of the previous
    # process (nothing announces it: only the validation finds it)
    file_write(site.joinpath(f'{DEP}/core.py'), b'damaged\n')
    server = servers.servers[UPDATE_HOST]
    publish(server, f'{CHANNEL}/packrepo', 't1', full_t1, index_t1)
    publish(server, f'{CHANNEL}/packdep/{DEP}', '1.0', dep_1_full, dep_1_index)
    publish(server, f'{CHANNEL}/packdep/{STABLE}', '1.0', stable_full, stable_index)

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
INFO | [Update] Full update finished, changed mods: []
INFO | [Restart] Flow "startup" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
            nursery.cancel_scope.cancel()
    # the damaged file was downloaded again from the channel
    assert file_read_bytes(f'{site}/{DEP}/core.py') == b"VERSION = '1.0'\n"
    # the unchanged dependency was validated and left at its version
    assert file_read_bytes(f'{site}/{STABLE}/core.py') == b"VERSION = '1.0'\n"
