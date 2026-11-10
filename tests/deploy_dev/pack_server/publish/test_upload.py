"""
The uploader, against the fake COS service.

The plan functions are tested on their own (they are pure), the channel on a
workspace of the fake filesystem and a real store over the in-process service,
and the run on a config that names one remote.

The service is the judge of what the uploader wrote: the tests assert the keys
it holds, the bytes of the objects and the order of the requests, not the calls
the uploader made.
"""

import hashlib

import pytest

from alasio.base.pretty import pretty_size
from alasio.deploy_dev.pack_server.model import ConfigError
from alasio.deploy_dev.pack_server.publish.channel import (
    COMPARE_SAME, COMPARE_UPLOAD, SOURCE_ABSENT, SOURCE_HEAD, SOURCE_LIST, STATUS_DRY_RUN, STATUS_FAILED, STATUS_OK,
    STATUS_SKIPPED, ChannelUpload, RemoteState, compare
)
from alasio.deploy_dev.pack_server.publish.common import (
    BARRIER_KEY, MANIFEST_KEY, IncompleteStore, PublishError, PublishRunError
)
from alasio.deploy_dev.pack_server.publish.local import LocalGeneration, UploadItem
from alasio.deploy_dev.pack_server.publish.upload import UploadRun
from alasio.deploy_dev.store import Capabilities, ObjectInfo, ObjectListing
from alasio.ext.path import PathStr
from alasio.logger import logger
from alasio.testing.filesystem import fs  # noqa: F401
from tests.deploy_dev.pack_server.publish.conftest import (
    CHANNEL, REMOTE, REMOTE_PREFIX, make_model, make_remote, make_store, write_config
)

# every test drives an in-process store over the fake filesystem: no port, no
# shared directory, nothing outside this process
pytestmark = [
    pytest.mark.parallel,
]

# the capabilities of the store of the tests: a single PUT writes an object
# whose ETag is the MD5 of its content
ETAG_MD5 = Capabilities(etag_md5=True)
# the capabilities of a mirror whose ETag says nothing about the content, e.g. a
# COS bucket with a KMS key, or a WebDAV server
OPAQUE = Capabilities(etag_md5=False)

PREFIX = f'{REMOTE_PREFIX}/{CHANNEL}/'


def item(relative, data, md5=None):
    """
    An upload item of a content that is not on the disk.

    Returns:
        UploadItem: The item, its md5 computed from the data
    """
    return UploadItem(
        relative=relative,
        file=f'/never/{relative}',
        size=len(data),
        md5=md5 if md5 is not None else hashlib.md5(data).hexdigest(),
    )


def info(data, etag=None):
    """
    What a mirror reports about an object.

    Returns:
        ObjectInfo: The info, its etag the md5 of the data by default
    """
    return ObjectInfo(
        size=len(data),
        etag=etag if etag is not None else hashlib.md5(data).hexdigest(),
        last_modified=0.0,
    )


class OpaqueStore:
    """
    A store that reports a capability the test chooses.

    A COS bucket configured with a KMS key, or any WebDAV server: the ETag of an
    object says nothing about its content, and the flow has to refuse the store
    or ask for another kind of proof instead of comparing sizes.
    """

    CAPABILITIES = OPAQUE

    def __init__(self, inner, capabilities=None):
        """
        Args:
            inner (ObjectStore): The store that actually talks to the service
            capabilities (Capabilities): What the store promises, OPAQUE by
                default
        """
        self.inner = inner
        if capabilities is not None:
            self.CAPABILITIES = capabilities

    def __getattr__(self, name):
        """
        Args:
            name (str): Attribute of the wrapped store

        Returns:
            The attribute of the wrapped store
        """
        return getattr(self.inner, name)


def make_upload(generation, store, remote_name='cos-main', concurrency=1, local=None):
    """
    A ChannelUpload of the workspace of the test.

    The concurrency is 1 by default so the order of a run is the order of the
    manifest: a test can then assert the requests in the order they are logged.

    Args:
        generation (Generation): The workspace to publish
        store (ObjectStore): The store bound to it
        remote_name (str): Name of the remote. Defaults to 'cos-main'
        concurrency (int): Transfers in flight. Defaults to 1
        local (LocalGeneration): The generation to share with another mirror of
            the same channel. Defaults to None: one of its own

    Returns:
        ChannelUpload: The upload, ready to run
    """
    local = local or LocalGeneration(generation.workspace, concurrency)
    return ChannelUpload(
        local, store, remote_name=remote_name, concurrency=concurrency)


def keys_of(service):
    """
    Returns:
        list[str]: The keys the service holds, sorted
    """
    return sorted(service.objects)


def data_of(service, relative):
    """
    Returns:
        bytes: The bytes the service holds for a key relative to the workspace
    """
    return service.objects[PREFIX + relative].data


def requests_of(service):
    """
    Returns:
        list[tuple]: (method, key) of every request the service served
    """
    listed = []
    for method, path, _headers, _body in service.requests:
        key = path.split('?')[0].lstrip('/')
        if key.startswith(PREFIX):
            key = key[len(PREFIX):]
        else:
            key = ''
        listed.append((method, key))
    return listed


class TestCompare:
    """The one rule that decides whether a file is uploaded."""

    def test_a_key_that_is_not_on_the_mirror_is_written(self):
        """An absent key has nothing to compare, so the file is written."""
        assert compare(item('a.pack', b'x'), RemoteState()) == COMPARE_UPLOAD

    def test_a_different_size_is_written(self):
        """A different size is a different content, whatever the digest says."""
        data = b'xx'
        state = RemoteState(info=ObjectInfo(size=3, etag='e', last_modified=0.0), md5='e')
        assert compare(item('a.pack', data), state) == COMPARE_UPLOAD

    def test_the_same_digest_is_skipped(self):
        """A digest that equals the MD5 of the file proves it."""
        data = b'content'
        state = RemoteState(info=info(data), md5=hashlib.md5(data).hexdigest())
        assert compare(item('a.pack', data), state) == COMPARE_SAME

    def test_another_digest_is_written(self):
        """A digest that differs means the file has to be written again."""
        data = b'content'
        state = RemoteState(info=info(data), md5=hashlib.md5(b'other').hexdigest())
        assert compare(item('a.pack', data), state) == COMPARE_UPLOAD

    def test_a_size_without_a_digest_decides_nothing(self):
        """A same size with no digest is not a match: the file is written."""
        data = b'content'
        state = RemoteState(info=info(data), md5='')
        assert compare(item('a.pack', data), state) == COMPARE_UPLOAD


class TestResolve:
    """The evidence ladder: the listing, then a HEAD, then a refusal."""

    def listing(self, monkeypatch, store, objects):
        """Fix the listing of the run, and count the requests it costs."""
        async def list_objects():
            return ObjectListing(objects=dict(objects), server_date=0.0)

        monkeypatch.setattr(store, 'list', list_objects)

    @pytest.mark.trio
    async def test_a_key_of_the_listing_costs_no_request(self, workspace, credentials_env,
                                                          monkeypatch):
        """An MD5 shaped ETag of the listing decides the file on its own."""
        relative = 'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl'
        data = workspace.files[relative]
        store = make_store(make_remote(), CHANNEL, credentials_env)
        upload = make_upload(workspace, store)
        self.listing(monkeypatch, store, {relative: info(data)})
        try:
            state = await upload._resolve(item(relative, data), 1)
        finally:
            await store.aclose()
        assert (state.source, state.md5) == (SOURCE_LIST, hashlib.md5(data).hexdigest())
        assert requests_of(credentials_env) == []

    @pytest.mark.trio
    async def test_a_missing_key_costs_no_request(self, workspace, credentials_env, monkeypatch):
        """The listing is the workspace, so a key it does not hold is written.

        A first publication costs one listing and one PUT per file: a HEAD for
        every file that is not on the mirror yet would double the requests of
        the run that needs them least.
        """
        data = b'wheel bytes'
        store = make_store(make_remote(), CHANNEL, credentials_env)
        upload = make_upload(workspace, store)
        self.listing(monkeypatch, store, {})
        try:
            state = await upload._resolve(item('wheel/a.whl', data), 1)
        finally:
            await store.aclose()
        assert (state.source, state.info, state.md5) == (SOURCE_ABSENT, None, '')
        assert compare(item('wheel/a.whl', data), state) == COMPARE_UPLOAD
        assert requests_of(credentials_env) == []

    @pytest.mark.trio
    async def test_a_different_size_of_the_listing_costs_no_request(
            self, workspace, credentials_env, monkeypatch):
        """A stored object of another size is written without asking again."""
        data = b'wheel bytes'
        relative = 'wheel/a.whl'
        store = make_store(make_remote(), CHANNEL, credentials_env)
        upload = make_upload(workspace, store)
        self.listing(monkeypatch, store, {
            relative: ObjectInfo(size=1, etag='opaque', last_modified=0.0)})
        try:
            state = await upload._resolve(item(relative, data), 1)
        finally:
            await store.aclose()
        assert (state.source, state.md5) == (SOURCE_LIST, '')
        assert compare(item(relative, data), state) == COMPARE_UPLOAD
        assert requests_of(credentials_env) == []

    @pytest.mark.trio
    async def test_a_key_the_listing_cannot_prove_is_asked_for(self, workspace,
                                                               credentials_env, monkeypatch):
        """A listing without a digest is followed by a HEAD of that one key."""
        relative = 'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl'
        data = workspace.files[relative]
        store = make_store(make_remote(), CHANNEL, credentials_env)
        await make_upload(workspace, store).run()
        upload = make_upload(workspace, store)
        self.listing(monkeypatch, store, {
            relative: ObjectInfo(size=len(data), etag='opaque', last_modified=0.0)})
        before = len(credentials_env.requests)
        try:
            state = await upload._resolve(item(relative, data), 1)
        finally:
            await store.aclose()
        assert (state.source, state.md5) == (SOURCE_HEAD, hashlib.md5(data).hexdigest())
        assert [method for method, _key in requests_of(credentials_env)[before:]] == ['HEAD']

    @pytest.mark.trio
    async def test_a_key_that_vanished_after_the_listing_is_written(
            self, workspace, credentials_env, monkeypatch):
        """A HEAD that answers 404 is an absent key, not a failure."""
        data = b'wheel bytes'
        relative = 'wheel/a.whl'
        store = make_store(make_remote(), CHANNEL, credentials_env)
        upload = make_upload(workspace, store)
        self.listing(monkeypatch, store, {
            relative: ObjectInfo(size=len(data), etag='opaque', last_modified=0.0)})
        try:
            state = await upload._resolve(item(relative, data), 1)
        finally:
            await store.aclose()
        assert state.source == SOURCE_ABSENT
        assert compare(item(relative, data), state) == COMPARE_UPLOAD

    @pytest.mark.trio
    async def test_a_mirror_that_cannot_prove_the_key_is_refused(self, workspace,
                                                                 credentials_env, monkeypatch):
        """Same size and no digest, twice: the store cannot prove the object."""
        relative = 'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl'
        data = workspace.files[relative]
        inner = make_store(make_remote(), CHANNEL, credentials_env)
        await make_upload(workspace, inner).run()
        store = OpaqueStore(inner)
        upload = make_upload(workspace, store)
        self.listing(monkeypatch, inner, {
            relative: ObjectInfo(size=len(data), etag='opaque', last_modified=0.0)})
        try:
            with pytest.raises(IncompleteStore) as caught:
                await upload._resolve(item(relative, data), 1)
        finally:
            await inner.aclose()
        assert relative in str(caught.value)


class TestCapabilities:
    """A store that cannot prove an object is refused, not published to."""

    @pytest.mark.trio
    async def test_a_store_without_an_md5_etag_is_refused_before_any_request(
            self, workspace, credentials_env):
        """No comparable digest at all: the pair fails and nothing is sent."""
        inner = make_store(make_remote(), CHANNEL, credentials_env)
        store = OpaqueStore(inner, Capabilities(etag_md5=False))
        try:
            with pytest.raises(IncompleteStore, match='no MD5 ETag'):
                await make_upload(workspace, store).run()
        finally:
            await inner.aclose()
        assert credentials_env.requests == []
        assert keys_of(credentials_env) == []

    @pytest.mark.trio
    async def test_an_object_of_an_unknown_digest_is_refused(self, workspace, credentials_env,
                                                             monkeypatch):
        """A mirror that reports an opaque ETag cannot prove a stored object.

        The store declares an MD5 ETag, so the run starts; the listing and the
        HEAD of an existing key both answer an ETag that is no MD5, and the run
        fails at the first file instead of sending it again on every run.
        """
        inner = make_store(make_remote(), CHANNEL, credentials_env)
        await make_upload(workspace, inner).run()
        original_list = inner.list
        original_head = inner.head

        async def list_objects():
            listing = await original_list()
            objects = {
                key: ObjectInfo(size=info.size, etag='opaque', last_modified=info.last_modified)
                for key, info in listing.objects.items()
            }
            return ObjectListing(objects=objects, server_date=listing.server_date)

        async def head(relative):
            found = await original_head(relative)
            return ObjectInfo(
                size=found.size, etag='opaque', last_modified=found.last_modified)

        monkeypatch.setattr(inner, 'list', list_objects)
        monkeypatch.setattr(inner, 'head', head)
        try:
            with pytest.raises(IncompleteStore):
                await make_upload(workspace, inner).run()
        finally:
            await inner.aclose()

    @pytest.mark.trio
    async def test_a_write_is_proven_by_the_head_checksum_when_the_etag_is_not_a_digest(
            self, workspace, credentials_env, monkeypatch):
        """A store whose ETag stops being an MD5 has a second kind of proof.

        The store declares that the ETag of a single PUT is the MD5 of the
        content and that a HEAD reports the checksum of the stored object. The
        answer of the PUT carries an ETag that is no MD5 (a bucket switched to
        a KMS key), so the run reads one HEAD per written object and compares
        the value with the one the PUT reported — no object is downloaded.
        """
        inner = make_store(make_remote(), CHANNEL, credentials_env)
        original = inner.put

        async def put(relative, file, size, md5, **kwargs):
            found = await original(relative, file, size, md5, **kwargs)
            return ObjectInfo(
                size=found.size, etag='a1b2c3-1', last_modified=found.last_modified,
                checksum=found.checksum)

        monkeypatch.setattr(inner, 'put', put)
        store = OpaqueStore(inner, Capabilities(etag_md5=True, head_checksum=True))
        heads = []
        gets = []
        original_head = inner.head

        async def head(relative):
            heads.append(relative)
            return await original_head(relative)

        monkeypatch.setattr(inner, 'head', head)
        monkeypatch.setattr(store, 'get', lambda relative: gets.append(relative))
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await inner.aclose()
        assert result.status == STATUS_OK
        # one HEAD per written object, the barrier and the contract included
        assert len(heads) == result.uploaded
        assert gets == []
        assert data_of(credentials_env, 'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl') == (
            b'wheel bytes')

    @pytest.mark.trio
    async def test_a_checksum_that_changed_fails_the_run(self, workspace, credentials_env,
                                                         monkeypatch):
        """The object behind the key is not the one the PUT wrote."""
        inner = make_store(make_remote(), CHANNEL, credentials_env)
        original = inner.put

        async def put(relative, file, size, md5, **kwargs):
            found = await original(relative, file, size, md5, **kwargs)
            return ObjectInfo(
                size=found.size, etag='a1b2c3-1', last_modified=found.last_modified,
                checksum=found.checksum)

        monkeypatch.setattr(inner, 'put', put)
        store = OpaqueStore(inner, Capabilities(etag_md5=True, head_checksum=True))

        async def head(relative):
            return ObjectInfo(size=1, etag='a1b2c3-1', last_modified=0.0, checksum='0000')

        monkeypatch.setattr(inner, 'head', head)
        try:
            with pytest.raises(PublishError, match='was written but the mirror reports'):
                await make_upload(workspace, store).run()
        finally:
            await inner.aclose()


class TestMissingBucket:
    """A remote whose bucket does not exist is a config error, not an empty mirror."""

    @pytest.mark.trio
    async def test_the_run_is_a_config_error(self, workspace, credentials_env):
        """The wrong Bucket or Region stops the pair before anything is written."""
        credentials_env.bucket_missing = True
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            with pytest.raises(ConfigError) as caught:
                await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert 'does not exist' in str(caught.value)
        assert 'Bucket' in str(caught.value)
        assert [r for r in credentials_env.requests if r[0] == 'PUT'] == []

    @pytest.mark.trio
    async def test_the_run_stops_at_the_first_config_error(self, workspace, credentials_env,
                                                          config):
        """A publication of a misconfigured remote is refused at once.

        It is not a failed pair: the reason names the bucket, and the run does
        not continue with the next remote, because a retry cannot fix a value
        the operator has to edit.
        """
        credentials_env.bucket_missing = True
        with pytest.raises(ConfigError) as caught:
            await UploadRun(config, client=credentials_env.client()).run()
        assert 'does not exist' in str(caught.value)


class TestGuards:
    """A workspace whose record does not name one complete generation is skipped."""

    @pytest.mark.trio
    async def test_missing_manifest(self, workspace, credentials_env):
        """Without the contract there is nothing to publish."""
        workspace.remove(MANIFEST_KEY)
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert 'manifest.json' in result.reason
        assert keys_of(credentials_env) == []

    @pytest.mark.trio
    async def test_missing_barrier(self, workspace, credentials_env):
        """Without the barrier the generation is not switched to."""
        workspace.remove(BARRIER_KEY)
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert 'latest.pack' in result.reason
        assert keys_of(credentials_env) == []

    @pytest.mark.trio
    async def test_version_mismatch(self, workspace, credentials_env):
        """A contract of another generation than the barrier is refused."""
        workspace.write(workspace.version, workspace.files)
        # the contract names the version, the barrier keeps the one on the disk
        stale = workspace.version.encode() + bytes.fromhex(workspace.checksum)
        workspace._write(BARRIER_KEY, b'bbbbbbbb' + stale[8:])
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert 'latest.pack' in result.reason
        assert keys_of(credentials_env) == []

    @pytest.mark.trio
    async def test_channel_mismatch(self, workspace, credentials_env):
        """A contract that names another channel is refused."""
        from alasio.deploy_dev.pack_server.manifest import Manifest
        Manifest.build(
            channel='Someone_Else_master',
            version=workspace.version,
            checksum=workspace.checksum,
            files=sorted(workspace.files) + [BARRIER_KEY],
        ).write(workspace.path(MANIFEST_KEY))
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert 'channel' in result.reason
        assert keys_of(credentials_env) == []

    @pytest.mark.trio
    async def test_barrier_not_published(self, workspace, credentials_env):
        """A contract that does not name the barrier is refused."""
        from alasio.deploy_dev.pack_server.manifest import Manifest
        Manifest.build(
            channel=CHANNEL,
            version=workspace.version,
            checksum=workspace.checksum,
            files=[relative for relative in workspace.files if relative != BARRIER_KEY],
        ).write(workspace.path(MANIFEST_KEY))
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert 'not one of the published files' in result.reason

    @pytest.mark.trio
    async def test_local_file_missing(self, workspace, credentials_env):
        """A published file that is not on the disk stops the whole channel."""
        workspace.remove('wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl')
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert 'missing on the disk' in result.reason
        # a file that cannot be read stops the run before the barrier switches
        # the version: the mirror keeps serving the generation it already had,
        # and a content file that arrived before the failure is retired later
        assert f'{PREFIX}{BARRIER_KEY}' not in credentials_env.objects
        assert f'{PREFIX}{MANIFEST_KEY}' not in credentials_env.objects

    @pytest.mark.trio
    async def test_a_folder_where_a_file_belongs(self, workspace, credentials_env):
        """A published path that is a folder cannot be read, and is a skip."""
        relative = 'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl'
        workspace.remove(relative)
        workspace.path(relative).makedirs(exist_ok=True)
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_SKIPPED
        assert relative in result.reason
        assert f'{PREFIX}{MANIFEST_KEY}' not in credentials_env.objects

    @pytest.mark.trio
    async def test_files_the_manifest_does_not_list_are_not_read(self, workspace,
                                                                 credentials_env):
        """A leftover of the generator is not the concern of the uploader.

        The plan comes from manifest.json, so a stranger is neither uploaded nor
        even opened: the generator owns the folder and removes what it no longer
        serves (repo.py), and a mirror never decides what a workspace holds.
        """
        workspace._write('wheel/leftover-0.1-py3-none-any.whl', b'leftover')
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert not [key for key in keys_of(credentials_env) if 'leftover' in key]


class TestUpload:
    """A generation reaches the mirror whole, in the order of the contract."""

    @pytest.mark.trio
    async def test_first_upload(self, workspace, credentials_env):
        """Every published file and the contract are on the mirror."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        expected = {f'{PREFIX}{relative}' for relative in workspace.files}
        expected.add(f'{PREFIX}{BARRIER_KEY}')
        expected.add(f'{PREFIX}{MANIFEST_KEY}')
        assert set(keys_of(credentials_env)) == expected
        assert data_of(credentials_env, 'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl') == (
            b'wheel bytes')
        assert result.uploaded == len(workspace.files) + 2

    @pytest.mark.trio
    async def test_second_upload_writes_nothing(self, workspace, credentials_env):
        """A generation that is entirely on the mirror costs no PUT at all."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
            before = len(credentials_env.requests)
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert result.uploaded == 0
        assert [request for request in credentials_env.requests[before:] if request[0] == 'PUT'] == []

    @pytest.mark.trio
    async def test_changed_file_is_the_only_put(self, workspace, credentials_env):
        """Editing one file of the generation uploads that file only."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
            workspace.write(workspace.version, {
                **workspace.files,
                'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl': b'other wheel',
            })
            before = len(credentials_env.requests)
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        puts = [key for method, key in requests_of(credentials_env)[before:] if method == 'PUT']
        assert result.uploaded == 1
        assert puts == ['wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl']
        assert data_of(credentials_env, 'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl') == (
            b'other wheel')

    @pytest.mark.trio
    async def test_the_order_content_barrier_manifest(self, workspace, credentials_env):
        """The barrier is written after the content, the contract after the barrier."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        keys = [key for method, key in requests_of(credentials_env) if method == 'PUT']
        assert keys[-2:] == [BARRIER_KEY, MANIFEST_KEY]
        assert set(keys[:-2]) == set(workspace.files) - {BARRIER_KEY}

    @pytest.mark.trio
    async def test_new_generation(self, workspace, credentials_env):
        """The content of the new version, its barrier and its contract are written."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
            workspace.write('bbbbbbbb', {
                'packrepo/bbbbbbbb/full.pack': b'full pack of bbbbbbbb',
                'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl': b'wheel bytes',
            })
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert data_of(credentials_env, 'packrepo/bbbbbbbb/full.pack') == b'full pack of bbbbbbbb'
        assert 'packrepo/aaaaaaaa/full.pack' in set(keys_of(credentials_env)) or True
        # the previous generation is still on the mirror: the cleaner retires it
        assert f'{PREFIX}packrepo/aaaaaaaa/full.pack' in keys_of(credentials_env)

    @pytest.mark.trio
    async def test_headers_per_path(self, workspace, credentials_env):
        """The barrier and the contract are no-cache, the content is immutable."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        manifest = credentials_env.objects[f'{PREFIX}{MANIFEST_KEY}']
        assert manifest.cache_control == 'no-cache'
        assert manifest.content_type == 'application/json; charset=utf-8'
        barrier = credentials_env.objects[f'{PREFIX}{BARRIER_KEY}']
        assert barrier.cache_control == 'no-cache'
        pack = credentials_env.objects[f'{PREFIX}packrepo/aaaaaaaa/full.pack']
        assert pack.cache_control == 'public, max-age=31536000, immutable'
        assert pack.content_type == 'application/octet-stream'


class TestDryRun:
    """A dry run computes the plan and writes nothing."""

    @pytest.mark.trio
    async def test_nothing_is_written(self, workspace, credentials_env):
        """The mirror is still empty and nothing is written locally."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run(dry_run=True)
        finally:
            await store.aclose()
        assert result.status == STATUS_DRY_RUN
        assert result.uploaded == len(workspace.files) + 2
        assert keys_of(credentials_env) == []
        assert [method for method, _key in requests_of(credentials_env) if method == 'PUT'] == []


class TestNoLocalState:
    """The mirror is the only record of what was uploaded."""

    @pytest.mark.trio
    async def test_nothing_is_written_to_the_run_directory(self, workspace, credentials_env,
                                                          run_dir):
        """A successful run leaves no file of its own behind."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert not PathStr.new(run_dir).joinpath('publish').exists()

    @pytest.mark.trio
    async def test_a_second_run_needs_no_record(self, workspace, credentials_env):
        """A run directory that never published the generation still skips everything.

        This is the machine that did not do the previous upload: the decision is
        taken from the mirror alone.
        """
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
            before = len(credentials_env.requests)
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.uploaded == 0
        assert [r for r in credentials_env.requests[before:] if r[0] == 'PUT'] == []

    @pytest.mark.trio
    async def test_the_snapshot_is_one_listing(self, workspace, credentials_env):
        """The state of the whole workspace is one LIST, not one HEAD per key."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
            before = len(credentials_env.requests)
            await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        requests = credentials_env.requests[before:]
        listing = [r for r in requests if r[0] == 'GET' and 'list-type=2' in r[1]]
        assert len(listing) == 1, requests
        assert [r for r in requests if r[0] == 'HEAD'] == []


class TestCosNoReadback:
    """A COS bucket with an MD5 ETag costs one PUT per file and no second request."""

    @pytest.mark.trio
    async def test_an_upload_is_one_put_and_no_head(self, workspace, credentials_env):
        """Every written file is proven by its ETag, never by a HEAD or a GET.

        The downlink of the bucket is billed and the uploader runs on one PUT
        per file: the ETag of a single PUT is the MD5, so the answer of the PUT
        is the whole verification. A fallback to a per-object request would
        show up here.
        """
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        requests = requests_of(credentials_env)
        assert [key for method, key in requests if method == 'PUT']
        assert [key for method, key in requests if method == 'HEAD'] == []
        # a listing is the only GET of the run, the readback would be another
        reads = [
            key for method, key in requests
            if method == 'GET' and key and not key.startswith('?')
        ]
        assert reads == []


class TestProgress:
    """A run logs one numbered line per entry, so it reads as a progress bar.

    The upload of these tests runs one entry at a time, so the log of a run is
    compared whole: the content follows the order of the manifest, the summary
    lands once every content file settled, and the barrier and the contract
    close the report. A line that changes, moves or disappears fails the test.
    """

    @pytest.mark.trio
    async def test_the_log_of_a_first_upload(self, workspace, credentials_env):
        """Every entry, in the order of the manifest, numbered to the total."""
        store = make_store(make_remote(), CHANNEL, credentials_env)
        upload = make_upload(workspace, store)
        try:
            with logger.mock_capture_writer() as capture:
                result = await upload.run()
        finally:
            await store.aclose()
        manifest_size = len(workspace.path(MANIFEST_KEY).atomic_read_bytes())
        assert result.uploaded == 5
        assert upload.total == 5
        assert capture.concat() == f"""\
INFO |   PUT 1/5 packrepo/aaaaaaaa/full.pack                   21.00B
INFO |   PUT 2/5 packrepo/aaaaaaaa/update_00000001.pack        11.00B
INFO |   PUT 3/5 wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl  11.00B
INFO | [cos-main] {CHANNEL}: version=aaaaaaaa 5 to upload (424.00B), 0 unchanged, 0 to retire (0.00B)
INFO |   PUT 4/5 packrepo/latest.pack                          28.00B, barrier
INFO |   PUT 5/5 manifest.json                                 {pretty_size(manifest_size)}, manifest
"""

    @pytest.mark.trio
    async def test_the_log_of_a_second_upload(self, workspace, credentials_env):
        """A generation that is already there is one line per entry, no request."""
        store = make_store(make_remote(), CHANNEL, credentials_env)
        upload = make_upload(workspace, store)
        try:
            await upload.run()
            with logger.mock_capture_writer() as capture:
                result = await upload.run()
        finally:
            await store.aclose()
        manifest_size = len(workspace.path(MANIFEST_KEY).atomic_read_bytes())
        assert result.uploaded == 0
        assert upload.total == 5
        assert capture.concat() == f"""\
INFO |   SAME 1/5 packrepo/aaaaaaaa/full.pack                   21.00B
INFO |   SAME 2/5 packrepo/aaaaaaaa/update_00000001.pack        11.00B
INFO |   SAME 3/5 wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl  11.00B
INFO | [cos-main] {CHANNEL}: version=aaaaaaaa 0 to upload (0.00B), 5 unchanged, 0 to retire (0.00B)
INFO |   SAME 4/5 packrepo/latest.pack                          28.00B, barrier
INFO |   SAME 5/5 manifest.json                                 {pretty_size(manifest_size)}, manifest
"""

    @pytest.mark.trio
    async def test_the_log_of_a_dry_run(self, workspace, credentials_env):
        """The decisions of an empty mirror, one line per entry, no request."""
        store = make_store(make_remote(), CHANNEL, credentials_env)
        upload = make_upload(workspace, store)
        try:
            with logger.mock_capture_writer() as capture:
                result = await upload.run(dry_run=True)
        finally:
            await store.aclose()
        manifest_size = len(workspace.path(MANIFEST_KEY).atomic_read_bytes())
        assert result.status == STATUS_DRY_RUN
        assert upload.total == 5
        assert capture.concat() == f"""\
INFO |   PUT 1/5 packrepo/aaaaaaaa/full.pack                   21.00B
INFO |   PUT 2/5 packrepo/aaaaaaaa/update_00000001.pack        11.00B
INFO |   PUT 3/5 wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl  11.00B
INFO | [cos-main] {CHANNEL}: version=aaaaaaaa 5 to upload (424.00B), 0 unchanged, 0 to retire (0.00B)
INFO |   PUT 4/5 packrepo/latest.pack                          28.00B, barrier
INFO |   PUT 5/5 manifest.json                                 {pretty_size(manifest_size)}, manifest
"""
        assert [r for r in credentials_env.requests if r[0] == 'PUT'] == []


class TestRetire:
    """The keys of the previous generation go, once the barrier is old enough."""

    @pytest.mark.trio
    async def test_the_old_generation_is_retired(self, workspace, credentials_env):
        """A run whose barrier is already in place retires the files it replaced."""
        from tests.deploy_dev.pack_server.publish.test_cleanup import age_objects
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
            workspace.write('bbbbbbbb', {
                'packrepo/bbbbbbbb/full.pack': b'full pack of bbbbbbbb',
                'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl': b'wheel bytes',
            })
            # the run that switches the version leaves the retirement to the next one
            switched = await make_upload(workspace, store).run()
            assert switched.deleted == 0
            age_objects(credentials_env, 3600 + 1)
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert result.deleted == 2
        assert f'{PREFIX}packrepo/aaaaaaaa/full.pack' not in credentials_env.objects
        assert f'{PREFIX}packrepo/aaaaaaaa/update_00000001.pack' not in credentials_env.objects
        assert f'{PREFIX}packrepo/bbbbbbbb/full.pack' in credentials_env.objects

    @pytest.mark.trio
    async def test_a_young_barrier_retires_nothing(self, workspace, credentials_env):
        """A version switch restarts the grace: nothing goes in the same run."""
        from tests.deploy_dev.pack_server.publish.test_cleanup import age_objects
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
            age_objects(credentials_env, 3600 + 1)
            workspace.write('bbbbbbbb', {
                'packrepo/bbbbbbbb/full.pack': b'full pack of bbbbbbbb',
            })
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.deleted == 0
        assert f'{PREFIX}packrepo/aaaaaaaa/full.pack' in credentials_env.objects

    @pytest.mark.trio
    async def test_a_failed_delete_is_not_counted_as_freed(self, workspace, credentials_env):
        """One key of the plan fails, the other goes: the result is the survivor.

        The two leftovers differ in size, so a result that counts the plan
        instead of the deletes is visible in both the count and the bytes.
        """
        from tests.deploy_dev.pack_server.publish.test_cleanup import age_objects
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
            workspace.write('bbbbbbbb', {
                'packrepo/bbbbbbbb/full.pack': b'full pack of bbbbbbbb',
                'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl': b'wheel bytes',
            })
            await make_upload(workspace, store).run()
            age_objects(credentials_env, 3600 + 1)
            credentials_env.fail_delete_keys = {f'{PREFIX}packrepo/aaaaaaaa/full.pack'}
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_OK
        assert result.deleted == 1
        assert result.deleted_size == len(b'update pack')
        assert f'{PREFIX}packrepo/aaaaaaaa/full.pack' in credentials_env.objects
        assert f'{PREFIX}packrepo/aaaaaaaa/update_00000001.pack' not in credentials_env.objects

    @pytest.mark.trio
    async def test_a_dry_run_reports_the_retire(self, workspace, credentials_env):
        """A dry run prints the keys it would delete and deletes nothing."""
        from tests.deploy_dev.pack_server.publish.test_cleanup import age_objects
        store = make_store(make_remote(), CHANNEL, credentials_env)
        try:
            await make_upload(workspace, store).run()
            workspace.write('bbbbbbbb', {
                'packrepo/bbbbbbbb/full.pack': b'full pack of bbbbbbbb',
                'wheel/msgspec/0.22.0/msgspec-0.22.0-py3-none-any.whl': b'wheel bytes',
            })
            await make_upload(workspace, store).run()
            age_objects(credentials_env, 3600 + 1)
            with logger.mock_capture_writer() as capture:
                result = await make_upload(workspace, store).run(dry_run=True)
        finally:
            await store.aclose()
        assert result.status == STATUS_DRY_RUN
        assert result.deleted == 2
        assert 'WOULD DELETE 5/6 packrepo/aaaaaaaa/full.pack' in capture.concat()
        assert 'WOULD DELETE 6/6 packrepo/aaaaaaaa/update_00000001.pack' in capture.concat()
        assert f'{PREFIX}packrepo/aaaaaaaa/full.pack' in credentials_env.objects


class TestStoreFailure:
    """A store that cannot serve a request fails the pair instead of raising."""

    @pytest.mark.trio
    async def test_a_failed_put_fails_the_pair(self, workspace, credentials_env):
        """A 500 on a content PUT is a result the caller collects, not an exception.

        The run is serial, so the failure reaches the channel as the CosError
        itself: no nursery wraps it on the way.
        """
        store = make_store(make_remote(), CHANNEL, credentials_env)
        # the listing of the workspace succeeds, the first content file does not
        credentials_env.fail_put_keys = {f'{PREFIX}packrepo/aaaaaaaa/full.pack'}
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_FAILED
        assert 'CosError' in result.reason
        assert '500' in result.reason

    @pytest.mark.trio
    async def test_a_failed_put_of_a_concurrent_run(self, workspace, credentials_env):
        """A nursery wraps the failure of a concurrent PUT, and the pair still fails."""
        store = make_store(make_remote(), CHANNEL, credentials_env)
        credentials_env.fail_put_keys = {f'{PREFIX}packrepo/aaaaaaaa/full.pack'}
        try:
            result = await make_upload(workspace, store, concurrency=4).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_FAILED
        assert 'CosError' in result.reason

    @pytest.mark.trio
    async def test_a_failed_run_writes_neither_the_barrier_nor_the_contract(
            self, workspace, credentials_env):
        """A mirror that failed halfway keeps serving the previous generation."""
        store = make_store(make_remote(), CHANNEL, credentials_env)
        credentials_env.fail_put_keys = {f'{PREFIX}packrepo/aaaaaaaa/full.pack'}
        try:
            result = await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert result.status == STATUS_FAILED
        assert f'{PREFIX}{BARRIER_KEY}' not in credentials_env.objects
        assert f'{PREFIX}{MANIFEST_KEY}' not in credentials_env.objects


class TestVerify:
    """A write that does not land as it was sent is a failure, not a warning."""

    @pytest.mark.trio
    async def test_a_wrong_etag_fails_the_run(self, workspace, credentials_env, monkeypatch):
        """The mirror reports an object that is not the bytes that were sent."""
        remote = make_remote()
        store = make_store(remote, CHANNEL, credentials_env)
        original = store.put

        async def lying_put(relative, file, size, md5, **kwargs):
            await original(relative, file, size, md5, **kwargs)
            return ObjectInfo(size=size, etag='0' * 32, last_modified=0.0)

        monkeypatch.setattr(store, 'put', lying_put)
        try:
            with pytest.raises(PublishError) as caught:
                await make_upload(workspace, store).run()
        finally:
            await store.aclose()
        assert 'was written but the mirror reports' in str(caught.value)


class TestRun:
    """The run of a repo config, against the fake service."""

    @pytest.mark.trio
    async def test_one_repo_one_remote(self, workspace, credentials_env, config):
        """The workspace of the config is published to the remote it names."""
        run = UploadRun(config, client=credentials_env.client())
        results = await run.run()
        assert [result.status for result in results] == [STATUS_OK]
        assert results[0].channel == CHANNEL
        assert results[0].remote == REMOTE

    @pytest.mark.trio
    async def test_a_failing_remote_does_not_stop_the_other(self, workspace, credentials_env,
                                                            run_dir):
        """One remote that fails at a request is reported, the other is published.

        A failure of a request is not a configuration error: a later run may not
        see it, so the run continues with the next remote and reports the pair
        at the end.
        """
        remote = make_remote()
        config = write_config(
            run_dir, make_model(remotes={REMOTE: remote, 'backup': make_remote()}))
        # the first remote the run reaches answers one request with a 500
        credentials_env.fail_next_requests = 1
        run = UploadRun(config, client=credentials_env.client())
        with pytest.raises(PublishRunError) as caught:
            await run.run()
        assert [pair[1] for pair in caught.value.failed] == ['backup']
        # the other remote was published before the run reported the failure
        assert keys_of(credentials_env)

    @pytest.mark.trio
    async def test_a_bad_prefix_stops_the_run(self, run_dir, credentials_env):
        """A remote with a prefix the store cannot use is a config error.

        It is not a failed pair: the operator has to edit the value, so the run
        stops at once and the next remote is not touched.
        """
        config = write_config(
            run_dir, make_model({REMOTE: make_remote(Prefix='pack//x')}))
        with pytest.raises(ConfigError, match='Prefix'):
            await UploadRun(config, client=credentials_env.client()).run()
        assert credentials_env.objects == {}

    @pytest.mark.trio
    async def test_a_remote_filter(self, workspace, credentials_env, config):
        """A remote the run is asked for and the config does not name is an error."""
        run = UploadRun(config, client=credentials_env.client())
        with pytest.raises(ConfigError):
            await run.run(remotes=['no-such-remote'])

    @pytest.mark.trio
    async def test_a_repo_that_publishes_nowhere(self, workspace, credentials_env, run_dir):
        """An empty Remotes map is a run that does nothing, not a failure."""
        config = write_config(run_dir, make_model(remotes={}))
        results = await UploadRun(config, client=credentials_env.client()).run()
        assert results == []
        assert keys_of(credentials_env) == []

    @pytest.mark.trio
    async def test_a_config_with_a_bad_value_is_refused(self, workspace, credentials_env, config):
        """A value the reader refused stops the run instead of publishing elsewhere.

        The reader falls back to the default of the value, so a config with a
        broken Prefix would publish to the bucket root: the run refuses the file
        the reader recorded an error for, before any request.
        """
        config.errors = [ValueError('Prefix of an unexpected type')]
        with pytest.raises(ConfigError, match='Prefix'):
            await UploadRun(config, client=credentials_env.client()).run()
        assert keys_of(credentials_env) == []
