"""
Scenario four of the realcase runs: the python dependencies a mod updates by pack

The real ``FlowStartup`` over the real backend of the conftest, with the real
``InstallBase``: the requirements file of a mod belongs to its tree and pins
the distributions it runs with, the ones the entry declares for pack updates
draw from the pack channel of the mod (``packdep/{dep}``, a sibling of the
``packrepo`` channel of its tree). The full update of the startup reconciles
the dependencies of every managed mod after their trees: a pin the environment
does not satisfy is installed, a pin it satisfies is verified and repaired --
a dependency that has no update is validated all the same.

The environment the dependencies land in is the site-packages of the process,
which a test must not touch: ``local_site_packages`` of the step layer is
patched to a folder of the in-memory filesystem, everything else is the real
code (the real channels, the real deploy job of the install).
"""
import pytest
import trio

from alasio.backend.app.restart import restart_step as step_module
from alasio.deploy.pack.decode_base import PackDecodeBase
from alasio.deploy.pack.job_unpack import UnpackJob
from alasio.deploy_dev.pack.pack_full import PackFull
from alasio.deploy_dev.pack.pack_update import PackUpdate
from alasio.deploy_dev.pack.pack_wheel import PackWheel
from alasio.deploy_dev.pack.pack_wheel_update import PackWheelUpdate
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import file_read_bytes, file_write
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy.simple_pip.conftest import build_wheel, list_folders, site_packages
from tests.deploy_dev.pack.conftest import make_repo

from .conftest import UPDATE_HOST, assert_logs, at

# The tree of the scenario: its requirements file is part of it, so an update
# of the tree brings the pins of its dependencies. t1 pins demo 1.0, t2 pins
# demo 2.0
REPO = make_repo({
    't1': {
        'module/main.py': b'print("t1")\n',
        'requirements.txt': b'demo==1.0\n',
    },
    't2': {
        'module/main.py': b'print("t2")\n',
        'requirements.txt': b'demo==2.0\n',
    },
})
FULL_T1 = b''.join(PackFull(REPO, commit='t1').iter_pack_data())
INDEX_T1 = b''.join(PackFull(REPO, commit='t1').iter_packidx_data())
FULL_T2 = b''.join(PackFull(REPO, commit='t2').iter_pack_data())
INDEX_T2 = b''.join(PackFull(REPO, commit='t2').iter_packidx_data())
UPDATE_T1_T2 = b''.join(PackUpdate(PackFull(REPO, commit='t2'), 't1').iter_pack_data())

# The path prefix of the deployment on the mock host, and the mirror of the
# mod: the packrepo folder of its tree channel, the packdep folders of its
# dependencies are derived from it
CHANNEL = '/alas/LmeSzinc_Alasio_master'
MIRROR = f'http://{UPDATE_HOST}{CHANNEL}/packrepo'
# The dist-key of the dependency channel (the PEP 503 normalized name)
DEP = 'demo'


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
    server.static.register_file(f'{prefix}/{new}/from_{old}.pack', update)


def dep_wheel(version):
    """
    Build the wheel of the dependency at a version (on the fake filesystem).

    Args:
        version (str): Version of the dependency

    Returns:
        str: Path of the .whl file
    """
    return build_wheel(
        f'/w/{DEP}-{version}-py3-none-any.whl',
        {
            f'{DEP}/__init__.py': b'',
            f'{DEP}/core.py': f"VERSION = '{version}'\n".encode(),
        },
        name=DEP,
        version=version,
    )


def dep_pack(version):
    """
    The pack data of the dependency at a version.

    Args:
        version (str): Version of the dependency

    Returns:
        tuple[bytes, bytes]: The full pack and the index pack
    """
    pack = PackWheel(dep_wheel(version))
    return b''.join(pack.iter_pack_data()), bytes(pack.index_pack)


def dep_update(old, new):
    """
    The update pack of a version pair of the dependency.

    Args:
        old (str): Version the update applies to
        new (str): Version the update installs

    Returns:
        bytes: Update pack of the pair
    """
    return b''.join(PackWheelUpdate(PackWheel(dep_wheel(new)), PackWheel(dep_wheel(old))).iter_pack_data())


@pytest.mark.trio
async def test_a_tree_update_brings_the_dependencies_of_its_new_requirements(
        fs, project, servers, make_backend, monkeypatch):
    """
    A cold start finds an update of the mod: the tree is applied and its
    dependencies are reconciled after it -- the pin of the new requirements
    file is installed from the packdep channel of the deployment, then the flow
    hands the process over (the new packages are imported by the new process)
    """
    backend = make_backend(mods={'DepsMod': MIRROR}, configs={}, pack_deps={'DepsMod': [DEP]})
    site = PathStr.new(site_packages(fs))
    monkeypatch.setattr(step_module, 'local_site_packages', lambda: site)
    mod_root = project.mod_root('DepsMod')
    # the tree and the dependency of the process this one replaces
    await project.install('DepsMod', pack=FULL_T1)
    dep_1_full, dep_1_index = dep_pack('1.0')
    await UnpackJob(dep_1_full, root=site, name=DEP).run()
    server = servers.servers[UPDATE_HOST]
    publish(server, f'{CHANNEL}/packrepo', 't2', FULL_T2, INDEX_T2)
    publish_update(server, f'{CHANNEL}/packrepo', 't1', 't2', UPDATE_T1_T2)
    dep_2_full, dep_2_index = dep_pack('2.0')
    publish(server, f'{CHANNEL}/packdep/{DEP}', '2.0', dep_2_full, dep_2_index)
    publish_update(server, f'{CHANNEL}/packdep/{DEP}', '1.0', '2.0', dep_update('1.0', '2.0'))

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
INFO | [Update] The dependencies of "DepsMod" are at their pinned versions
INFO | [Update] Full update finished, changed mods: ['DepsMod']
INFO | [Restart] Resume credential announced to the supervisor
INFO | [Restart] Resume file written: {backend.resume_file} (owner=restart, 0 configs, 0 actions, updated_all_at={at(2, 0):.0f})
INFO | [Restart] Handover of the "startup" flow (startup convergence): 0 configs to resume
INFO | [Restart] Flow "startup" finished
""")
            nursery.cancel_scope.cancel()
    # the dependency of the new requirements file landed in the environment,
    # and no metadata directory of the old version stays next to it
    assert file_read_bytes(f'{site}/{DEP}/core.py') == b"VERSION = '2.0'\n"
    folders = list_folders(site)
    assert [folder for folder in folders if folder.startswith(f'{DEP}-')] == [f'{DEP}-2.0.dist-info']


@pytest.mark.trio
async def test_the_startup_check_verifies_a_dependency_without_an_update(
        fs, project, servers, make_backend, monkeypatch):
    """
    A cold start with nothing to apply: the tree is validated and the
    dependency -- which has no update either -- is verified by its channel all
    the same, a damaged file is downloaded again. The flows of a mod update
    validate the whole environment, not only what moved
    """
    backend = make_backend(mods={'DepsMod': MIRROR}, configs={}, pack_deps={'DepsMod': [DEP]})
    site = PathStr.new(site_packages(fs))
    monkeypatch.setattr(step_module, 'local_site_packages', lambda: site)
    mod_root = project.mod_root('DepsMod')
    await project.install('DepsMod', pack=FULL_T1)
    dep_1_full, dep_1_index = dep_pack('1.0')
    await UnpackJob(dep_1_full, root=site, name=DEP).run()
    # the file of the dependency is damaged after the install of the previous
    # process (nothing announces it: only the validation finds it)
    file_write(site.joinpath(f'{DEP}/core.py'), b'damaged\n')
    server = servers.servers[UPDATE_HOST]
    publish(server, f'{CHANNEL}/packrepo', 't1', FULL_T1, INDEX_T1)
    publish(server, f'{CHANNEL}/packdep/{DEP}', '1.0', dep_1_full, dep_1_index)

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
INFO | [Update] The dependencies of "DepsMod" are at their pinned versions
INFO | [Update] Full update finished, changed mods: []
INFO | [Restart] Flow "startup" finished
INFO | [Restart] The resume queue is empty, nothing to start
""")
            nursery.cancel_scope.cancel()
    # the damaged file was downloaded again from the channel
    assert file_read_bytes(f'{site}/{DEP}/core.py') == b"VERSION = '1.0'\n"
