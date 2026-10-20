"""Tests for the artifact verifier of the wheel job."""
import io
import json
import os
import tarfile
import zipfile

import pytest

from alasio.codegen.asar import AsarArchive
from alasio.deploy.simple_pip.whl_record import RecordManager
from alasio.deploy_dev.pack.encode_manifest import PackEncodeManifest
from alasio.deploy_dev.wheel.verify import (
    AsarVerifier, SdistVerifier, WheelVerifier, WheelVerifyError, check_build_inputs, collect_artifacts,
    verify_artifact, verify_asar, verify_wheel
)
from alasio.ext.path import PathStr
from alasio.ext.path.atomic import atomic_read_bytes, atomic_write
from alasio.testing.filesystem import fs  # noqa: F401

FOLDER = 'alasio'
WHEEL = '/repo/dist/demo-1.0.0-py3-none-any.whl'
SDIST = '/repo/dist/demo-1.0.0.tar.gz'
DIST_INFO = 'demo-1.0.0.dist-info'
FRONTEND = {
    'index.html': b'<html>',
    '_app/immutable/start.js': b'JS',
}
METADATA = b'Metadata-Version: 2.4\nName: demo\nVersion: 1.0.0\n\n'
WHEEL_META = b'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n'


def make_manifest(files):
    """
    Encode the manifest of a frontend file set.

    Args:
        files (dict[str, bytes]): {frontend-relative path: content}

    Returns:
        bytes: Manifest content
    """
    manifest = PackEncodeManifest()
    for path, content in files.items():
        manifest.add_file(f'{FOLDER}/deploy_data/frontend/{path}', content)
    return b''.join(manifest.iter_manifest_data())


def make_asar(files=None, package=None):
    """
    Build the app.asar of a wheel.

    Args:
        files (dict[str, bytes]): {archive path: content}, defaults to a
            minimal dist tree
        package (dict): Content of package.json, defaults to a main entry of
            the default tree

    Returns:
        bytes: Content of the archive
    """
    if files is None:
        files = {
            'dist/main/index.js': b'MAIN',
            'dist/preload/index.js': b'PRELOAD',
            'dist/renderer/index.html': b'<html>',
        }
    if package is None:
        package = {'main': 'dist/main/index.js'}
    with AsarArchive() as asar:
        for path, content in files.items():
            asar.add_file(data=content, arc_path=path)
        asar.add_file(data=json.dumps(package).encode(), arc_path='package.json')
        asar.write('/repo/webapp/release/app.asar')
    return atomic_read_bytes('/repo/webapp/release/app.asar')


def make_wheel(
    path=WHEEL,
    frontend=FRONTEND,
    manifest=None,
    asar=None,
    metadata=METADATA,
    wheel_meta=WHEEL_META,
    drop=(),
    tamper=(),
):
    """
    Build a wheel with the layout of the real build.

    Args:
        path (str): Target path
        frontend (dict[str, bytes]): Frontend files of the wheel
        manifest (bytes): Manifest content, defaults to the manifest of
            frontend
        asar (bytes): Content of app.asar, defaults to a minimal archive
        metadata (bytes): Content of METADATA
        wheel_meta (bytes): Content of WHEEL
        drop (tuple[str]): Members removed after the RECORD is calculated
        tamper (tuple[tuple[str, bytes]]): Members replaced after the RECORD
            is calculated

    Returns:
        str: Path of the wheel
    """
    if manifest is None:
        manifest = make_manifest(frontend)
    if asar is None:
        asar = make_asar()
    members = {
        f'{FOLDER}/__init__.py': b'',
        f'{FOLDER}/deploy_data/frontend-manifest.pack': manifest,
        f'{FOLDER}/deploy_data/webapp/app.asar': asar,
    }
    for relative, content in frontend.items():
        members[f'{FOLDER}/deploy_data/frontend/{relative}'] = content

    members[f'{DIST_INFO}/METADATA'] = metadata
    members[f'{DIST_INFO}/WHEEL'] = wheel_meta
    record = RecordManager()
    for name, content in members.items():
        record.add_content(name, content)
    record.add_content(f'{DIST_INFO}/RECORD', None)
    members[f'{DIST_INFO}/RECORD'] = record.dump_bytes()

    for name in drop:
        members.pop(name)
    for name, content in tamper:
        members[name] = content

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


def make_sdist(path=SDIST, base='demo-1.0.0', members=None):
    """
    Build an sdist with the layout of the real build.

    Args:
        path (str): Target path
        base (str): Root folder of the sdist
        members (dict[str, bytes]): {path relative to base: content}

    Returns:
        str: Path of the sdist
    """
    if members is None:
        members = {
            'PKG-INFO': METADATA,
            'pyproject.toml': b'[project]\nname = "demo"\nversion = "1.0.0"\n',
            f'{FOLDER}/__init__.py': b'',
        }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(f'{base}/{name}')
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    atomic_write(path, buffer.getvalue())
    return path


class TestVerifierClasses:
    def test_wheel_state(self, fs):
        """The verifier keeps the state of the wheel it checked."""
        verifier = WheelVerifier(make_wheel())
        report = verifier.verify()
        assert report.kind == 'wheel'
        assert verifier.dist_info == DIST_INFO
        assert verifier.name == 'demo'
        assert verifier.version == '1.0.0'
        assert verifier.frontend_files == 2
        assert verifier.asar_entries == 8
        assert verifier.problems == []

    def test_asar_state(self, fs):
        """The verifier keeps the entry table of the archive it checked."""
        verifier = AsarVerifier(make_asar())
        assert verifier.verify() == 8
        assert verifier.data_offset > 0
        assert 'dist/main/index.js' in ['/'.join(keys) for keys in verifier.entries]
        assert verifier.problems == []

    def test_sdist_state(self, fs):
        """The verifier keeps the state of the sdist it checked."""
        verifier = SdistVerifier(make_sdist())
        report = verifier.verify()
        assert report.kind == 'sdist'
        assert verifier.base == 'demo-1.0.0'
        assert verifier.name == 'demo'
        assert verifier.version == '1.0.0'
        assert verifier.problems == []


class TestVerifyWheel:
    def test_valid_wheel(self, fs):
        """A wheel with every member of the real build passes."""
        report = verify_wheel(make_wheel())
        assert report.kind == 'wheel'
        assert report.name == 'demo'
        assert report.version == '1.0.0'
        assert report.files == 8
        assert 'frontend 2 files' in report.extra

    def test_missing_frontend(self, fs):
        """A wheel without the frontend page fails."""
        file = make_wheel(drop=(f'{FOLDER}/deploy_data/frontend/index.html',))
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('index.html' in problem for problem in e.value.problems)

    def test_missing_manifest(self, fs):
        """A wheel without the frontend manifest fails."""
        file = make_wheel(drop=(f'{FOLDER}/deploy_data/frontend-manifest.pack',))
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('frontend-manifest.pack' in problem for problem in e.value.problems)

    def test_missing_asar(self, fs):
        """A wheel without the webapp fails."""
        file = make_wheel(drop=(f'{FOLDER}/deploy_data/webapp/app.asar',))
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('app.asar' in problem for problem in e.value.problems)

    def test_missing_metadata(self, fs):
        """A wheel without a dist-info METADATA fails."""
        file = make_wheel(drop=(f'{DIST_INFO}/METADATA',))
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('dist-info' in problem for problem in e.value.problems)

    def test_metadata_name_mismatch(self, fs):
        """The METADATA has to name the distribution of the file name."""
        file = make_wheel(metadata=b'Metadata-Version: 2.4\nName: other\nVersion: 1.0.0\n\n')
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('names other==1.0.0' in problem for problem in e.value.problems)

    def test_wheel_tag(self, fs):
        """The wheel has to be the pure Python py3-none-any one."""
        file = make_wheel(wheel_meta=b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: cp38-cp38-win_amd64\n\n')
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('py3-none-any' in problem for problem in e.value.problems)

    def test_record_hash_mismatch(self, fs):
        """A member that does not match its RECORD row fails."""
        file = make_wheel(tamper=((f'{FOLDER}/__init__.py', b'changed'),))
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('RECORD hash mismatch' in problem for problem in e.value.problems)

    def test_record_lists_missing_file(self, fs):
        """A RECORD row of a file that is not in the wheel fails."""
        file = make_wheel(drop=(f'{FOLDER}/__init__.py',))
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('not in the wheel' in problem for problem in e.value.problems)

    def test_not_a_zip(self, fs):
        """A file that is not a zip fails."""
        atomic_write('/repo/dist/bad-1.0.0-py3-none-any.whl', b'not a zip')
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel('/repo/dist/bad-1.0.0-py3-none-any.whl')
        assert any('not a zip archive' in problem for problem in e.value.problems)

    def test_frontend_mismatch(self, fs):
        """A frontend file that does not match its manifest row fails."""
        manifest = make_manifest({**FRONTEND, '_app/immutable/start.js': b'OLD'})
        file = make_wheel(manifest=manifest)
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('does not match the manifest' in problem for problem in e.value.problems)

    def test_manifest_lists_missing_file(self, fs):
        """A manifest row of a file that is not in the wheel fails."""
        manifest = make_manifest({**FRONTEND, 'ghost.js': b''})
        file = make_wheel(manifest=manifest)
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('listed in the manifest is missing' in problem for problem in e.value.problems)

    def test_unlisted_frontend_file(self, fs):
        """A frontend file that the manifest does not list fails."""
        file = make_wheel(
            frontend={**FRONTEND, 'extra.js': b'X'},
            manifest=make_manifest(FRONTEND),
        )
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('not listed in the manifest' in problem for problem in e.value.problems)

    def test_invalid_manifest(self, fs):
        """A manifest that does not decode fails."""
        file = make_wheel(manifest=b'MANI\x00broken')
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('Frontend manifest is invalid' in problem for problem in e.value.problems)

    def test_asar_root_layout(self, fs):
        """An asar without the dist/ prefix fails, package.json.main points into dist/."""
        asar = make_asar(
            files={'main/index.js': b'MAIN'},
            package={'main': 'main/index.js'},
        )
        file = make_wheel(asar=asar)
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('outside of "dist/"' in problem for problem in e.value.problems)

    def test_asar_main_missing(self, fs):
        """An asar whose package.json.main does not resolve fails."""
        asar = make_asar(package={'main': 'dist/missing.js'})
        file = make_wheel(asar=asar)
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('main is missing' in problem for problem in e.value.problems)

    def test_asar_unpacked_entry(self, fs):
        """An unpacked entry points at a file the wheel does not carry."""
        with AsarArchive() as asar:
            asar.add_file(data=b'MAIN', arc_path='dist/main/index.js')
            asar.add_file(data=b'PACKAGE', arc_path='package.json', unpack=True)
            asar.write('/repo/webapp/release/app.asar')
        file = make_wheel(asar=atomic_read_bytes('/repo/webapp/release/app.asar'))
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('unpacked' in problem for problem in e.value.problems)

    def test_asar_invalid(self, fs):
        """A member that is not an asar archive fails."""
        file = make_wheel(asar=b'not an asar')
        with pytest.raises(WheelVerifyError) as e:
            verify_wheel(file)
        assert any('not a valid archive' in problem for problem in e.value.problems)


class TestVerifyAsar:
    def test_truncated(self, fs):
        """An archive shorter than the frame is rejected."""
        with pytest.raises(WheelVerifyError) as e:
            verify_asar(b'asar')
        assert any('truncated' in problem for problem in e.value.problems)

    def test_no_main(self, fs):
        """A package.json without a main path is rejected."""
        with AsarArchive() as asar:
            asar.add_file(data=b'{}', arc_path='package.json')
            asar.write('/repo/webapp/release/app.asar')
        data = atomic_read_bytes('/repo/webapp/release/app.asar')
        with pytest.raises(WheelVerifyError) as e:
            verify_asar(data)
        assert any('has no main' in problem for problem in e.value.problems)

    def test_no_package_json(self, fs):
        """An archive without package.json is rejected."""
        with AsarArchive() as asar:
            asar.add_file(data=b'MAIN', arc_path='dist/main/index.js')
            asar.write('/repo/webapp/release/app.asar')
        data = atomic_read_bytes('/repo/webapp/release/app.asar')
        with pytest.raises(WheelVerifyError) as e:
            verify_asar(data)
        assert any('no package.json' in problem for problem in e.value.problems)


class TestVerifySdist:
    def test_valid_sdist(self, fs):
        """An sdist with the files of the real build passes."""
        report = verify_artifact(make_sdist())
        assert report.kind == 'sdist'
        assert report.name == 'demo'
        assert report.version == '1.0.0'
        assert report.files == 3

    def test_missing_pyproject(self, fs):
        """An sdist without pyproject.toml fails."""
        members = {'PKG-INFO': METADATA, f'{FOLDER}/__init__.py': b''}
        with pytest.raises(WheelVerifyError) as e:
            verify_artifact(make_sdist(members=members))
        assert any('pyproject.toml' in problem for problem in e.value.problems)

    def test_missing_package(self, fs):
        """An sdist without a file of the library fails."""
        members = {'PKG-INFO': METADATA, 'pyproject.toml': b''}
        with pytest.raises(WheelVerifyError) as e:
            verify_artifact(make_sdist(members=members))
        assert any('no file of the library' in problem for problem in e.value.problems)

    def test_path_traversal(self, fs):
        """An sdist member outside of the archive root fails."""
        members = {
            'PKG-INFO': METADATA,
            'pyproject.toml': b'',
            f'{FOLDER}/__init__.py': b'',
            '../evil.sh': b'',
        }
        with pytest.raises(WheelVerifyError) as e:
            verify_artifact(make_sdist(members=members))
        assert any('outside of the archive root' in problem for problem in e.value.problems)

    def test_name_mismatch(self, fs):
        """The PKG-INFO has to name the distribution of the file name."""
        file = make_sdist(path='/repo/dist/other-1.0.0.tar.gz', base='other-1.0.0')
        with pytest.raises(WheelVerifyError) as e:
            verify_artifact(file)
        assert any('names demo==1.0.0' in problem for problem in e.value.problems)

    def test_not_a_tarball(self, fs):
        """A file that is not a tar.gz fails."""
        atomic_write('/repo/dist/bad-1.0.0.tar.gz', b'not a tarball')
        with pytest.raises(WheelVerifyError) as e:
            verify_artifact('/repo/dist/bad-1.0.0.tar.gz')
        assert any('not a readable sdist' in problem for problem in e.value.problems)


class TestCheckBuildInputs:
    def test_empty_root(self, fs):
        """Every build input is reported when the root is empty."""
        problems = check_build_inputs('/repo')
        assert len(problems) == 6
        assert all(problem.startswith('Missing build input: ') for problem in problems)

    def test_complete(self, fs):
        """No problem when every build input is there."""
        for path in (
            'frontend/build/index.html',
            'frontend/build/_app/version.json',
            'webapp/dist/main/index.js',
            'webapp/dist/preload/index.js',
            'webapp/dist/renderer/index.html',
            'webapp/package.json',
        ):
            fs.create_file(f'/repo/{path}', contents=b'')
        assert check_build_inputs('/repo') == []

    def test_missing_one(self, fs):
        """A missing input is reported alone."""
        for path in (
            'frontend/build/index.html',
            'frontend/build/_app/version.json',
            'webapp/dist/main/index.js',
            'webapp/dist/preload/index.js',
            'webapp/dist/renderer/index.html',
        ):
            fs.create_file(f'/repo/{path}', contents=b'')
        assert check_build_inputs('/repo') == ['Missing build input: webapp/package.json']


class TestCollectArtifacts:
    def test_directory(self, fs):
        """A directory is expanded to the artifacts it holds."""
        make_wheel()
        make_sdist()
        fs.create_file('/repo/dist/notes.txt', contents=b'')
        files, problems = collect_artifacts(['/repo/dist'])
        assert [PathStr.new(file).name for file in files] == [
            PathStr.new(WHEEL).name, PathStr.new(SDIST).name,
        ]
        assert problems == []

    def test_files(self, fs):
        """An artifact path is taken as it is."""
        make_wheel()
        files, problems = collect_artifacts([WHEEL])
        assert files == [WHEEL]
        assert problems == []

    def test_missing_path(self, fs):
        """A path that is neither an artifact nor a directory is reported."""
        files, problems = collect_artifacts(['/repo/dist'])
        assert files == []
        assert problems == ['Not a wheel, an sdist, or a directory that holds them: /repo/dist']
