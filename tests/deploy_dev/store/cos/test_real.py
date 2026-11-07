"""
The store against the real COS bucket.

The tests of this module are skipped unless the credential of the bucket is
configured: ALAS2026_COS_SECRET_ID and ALAS2026_COS_SECRET_KEY must both be in
the environment. The bucket and the region fall back to the test bucket of the
project and are overridable:

    ALAS2026_COS_BUCKET    alas2026-1254325529
    ALAS2026_COS_REGION    ap-shanghai
    ALAS2026_COS_ENDPOINT  derived from the bucket and the region

Two groups:

- TestCosClientReal: the client itself, its five verbs and its signature;
- TestCosCapabilitiesReal: the properties the uploader and the cleaner are
  built on. The uploader proves an upload by Content-MD5 and reads the state of
  a workspace from the ETag of a listing; the cleaner decides the age of an
  object from LastModified and removes what a generation no longer serves. If
  one of these properties changes in the service, the plan of the publish flow
  has to change, and these tests are where that is noticed.

What is deliberately not tested here: the retry schedule and the body of a
retried PUT (a real service cannot be made to fail on demand, the offline suite
pins both), the multipart limit, and the appearance of a just-written object in
a listing (the listing is eventually consistent, so that assertion would be
flaky by design; the cleaner never relies on it, see the plan §5.5).

Every test writes under tests/ of the bucket, in a prefix of its own: its name
plus eight random hex characters, e.g.

    tests/test_put_get_roundtrip-3f9a1c02/

The objects of the prefix are deleted when the test ends. The tests/ prefix
itself is never removed: it is a shared folder of the bucket, other runs and
other machines write there too.
"""

import hashlib
import os
import re
import secrets
import shutil
import tempfile
import time

# httpx2 resolves the version of its transport with importlib.metadata at
# import time, and a fake filesystem of another test cannot answer that call:
# the transport is imported at collection, before any test patches the disk
import httpcore2  # noqa: F401
import pytest
import trio

from alasio.deploy_dev.store import ObjectNotFound
from alasio.deploy_dev.store.cos import CosError, CosStore

ENV_PREFIX = 'ALAS2026_COS'

# the shared folder of the bucket the tests write in. Its own prefix is never
# deleted, only the folder of one test is
TESTS_ROOT = 'tests'

SECRET_ID = os.environ.get(f'{ENV_PREFIX}_SECRET_ID', '')
SECRET_KEY = os.environ.get(f'{ENV_PREFIX}_SECRET_KEY', '')
BUCKET = os.environ.get(f'{ENV_PREFIX}_BUCKET', 'alas2026-1254325529')
REGION = os.environ.get(f'{ENV_PREFIX}_REGION', 'ap-shanghai')
ENDPOINT = os.environ.get(f'{ENV_PREFIX}_ENDPOINT') or None

CONFIGURED = bool(SECRET_ID and SECRET_KEY)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not CONFIGURED,
        reason=f'{ENV_PREFIX}_SECRET_ID / {ENV_PREFIX}_SECRET_KEY are not set',
    ),
    # every test writes under a prefix of its own (tests/{name}-{8 hex}/) and
    # removes it again, so two of these files would not meet in the bucket; the
    # plugin keeps the items of one file in one worker anyway, i.e. serial
    # within the file, and only lets it run beside another marked file
    pytest.mark.parallel,
]


def make_store(prefix, **kwargs):
    """
    Build a store of the configured bucket.

    Args:
        prefix (str): Key prefix of the store
        **kwargs: Overrides of the constructor arguments (secret_id, secret_key,
            token, endpoint, client, ...)

    Returns:
        CosStore: The store
    """
    arguments = dict(
        bucket=BUCKET,
        region=REGION,
        secret_id=SECRET_ID,
        secret_key=SECRET_KEY,
        endpoint=ENDPOINT,
        prefix=prefix,
    )
    arguments.update(kwargs)
    return CosStore(**arguments)


def folder_of(request):
    """
    The prefix of one test: its name and random characters.

    Args:
        request (pytest.FixtureRequest): The request of the test

    Returns:
        str: E.g. 'tests/test_put_get_roundtrip-3f9a1c02/'
    """
    name = re.sub(r'[^A-Za-z0-9]+', '-', request.node.name).strip('-')
    return f'{TESTS_ROOT}/{name}-{secrets.token_hex(4)}/'


async def delete_folder(store, timeout=30.0):
    """
    Delete every object of the prefix of a store, until the listing is empty.

    The prefix of the test is the only thing removed: the listing of the store
    is bounded by its own prefix, so tests/ and the folders of other tests are
    out of reach.

    The listing of COS is eventually consistent: a key that was just deleted
    stays in a listing for a second or two (measured: still listed at 1.2 s,
    gone at 2.4 s). The deletes are repeated until a listing comes back empty,
    otherwise the next test that writes a folder of the same name would find
    half of the previous one.

    Args:
        store (CosStore): The store bound to the folder of the test
        timeout (float): Seconds to keep deleting. Defaults to 30.0

    Raises:
        AssertionError: If an object survives the deadline
    """
    deadline = time.time() + timeout
    while True:
        remaining = (await store.list()).objects
        if not remaining:
            return
        for relative in remaining:
            await store.delete(relative)
        if time.time() >= deadline:
            raise AssertionError(
                f'objects left in the test folder after {timeout}s: {sorted(remaining)}')
        await trio.sleep(0.5)


@pytest.fixture
async def probe(request):
    """
    A store bound to a folder of its own under tests/ of the bucket.

    Yields:
        CosStore: The store, its folder is emptied when the test ends
    """
    store = make_store(folder_of(request))
    try:
        yield store
    finally:
        try:
            await delete_folder(store)
        finally:
            await store.aclose()


@pytest.fixture
def make_file():
    """
    Write a local file of the test.

    The file is a real file on the disk: these tests speak https to the bucket,
    and the ssl context of the client reads the certificate store of the
    machine. A temporary folder per test keeps the files out of the project.

    Yields:
        Callable[[str, bytes], str]: Write a content and return its path
    """
    folder = tempfile.mkdtemp(prefix='alasio-cos-test-')
    try:
        def write(name, data):
            """
            Args:
                name (str): File name
                data (bytes): Content of the file

            Returns:
                str: Path of the file
            """
            path = os.path.join(folder, name)
            with open(path, 'wb') as f:
                f.write(data)
            return path

        yield write
    finally:
        shutil.rmtree(folder, ignore_errors=True)


async def put(store, make_file, relative, data, **kwargs):
    """
    Upload a content to the bucket.

    Args:
        store (CosStore): The store to upload to
        make_file (Callable): The file writer of the test
        relative (str): Relative key of the object
        data (bytes): Content of the object
        **kwargs: Extra arguments of CosStore.put()

    Returns:
        ObjectInfo: What the server reports about the object
    """
    path = make_file('upload.bin', data)
    return await store.put(relative, path, len(data), hashlib.md5(data).hexdigest(), **kwargs)


class TestCosClientReal:
    """The client itself: the five verbs and the signature."""

    @pytest.mark.trio
    async def test_put_get_roundtrip(self, probe, make_file):
        """The bytes that are sent are the bytes that are read back."""
        data = b'hello real cos ' * 128
        info = await put(probe, make_file, 'v1/data.bin', data)
        assert info.size == len(data)
        assert info.etag == hashlib.md5(data).hexdigest()
        assert await probe.get('v1/data.bin') == data

    @pytest.mark.trio
    async def test_nested_and_unicode_key(self, probe, make_file):
        """A key of several segments and non-ascii characters survives the wire.

        The key carries a '+', which yarl leaves unescaped in a path and COS
        reads as a space: the store escapes every segment itself, and this is
        the test that says so against the real service.
        """
        data = b'unicode key'
        relative = 'v1/中文 目录/a+b.txt'
        await put(probe, make_file, relative, data)
        assert await probe.get(relative) == data
        assert sorted((await probe.list()).objects) == [relative]

    @pytest.mark.trio
    async def test_empty_object(self, probe, make_file):
        """A zero byte object is a valid object."""
        info = await put(probe, make_file, 'v1/empty.bin', b'')
        assert info.size == 0
        assert await probe.get('v1/empty.bin') == b''

    @pytest.mark.trio
    async def test_overwrite(self, probe, make_file):
        """A key written twice holds the second content."""
        first = await put(probe, make_file, 'v1/data.bin', b'first')
        second = await put(probe, make_file, 'v1/data.bin', b'second')
        assert first.etag != second.etag
        assert await probe.get('v1/data.bin') == b'second'

    @pytest.mark.trio
    async def test_head_and_stat(self, probe, make_file):
        """HEAD and a listing agree on the size and the ETag."""
        data = b'head and stat'
        written = await put(probe, make_file, 'v1/data.bin', data)
        head = await probe.head('v1/data.bin')
        stat = await probe.stat(['v1/data.bin', 'v1/missing.bin'])
        assert head.size == stat['v1/data.bin'].size == len(data)
        assert head.etag == stat['v1/data.bin'].etag == written.etag
        assert sorted(stat) == ['v1/data.bin']

    @pytest.mark.trio
    async def test_list_pagination(self, probe, make_file):
        """The listing follows the continuation token of a page.

        A page size of one forces a page per object: a client that ignores the
        token sees one key, the test expects three.
        """
        probe.PAGE_SIZE = 1
        expected = ['v1/f0.bin', 'v1/f1.bin', 'v1/f2.bin']
        for index, relative in enumerate(expected):
            await put(probe, make_file, relative, b'x' * (index + 1))
        assert sorted((await probe.list()).objects) == expected

    @pytest.mark.trio
    async def test_large_object(self, probe, make_file):
        """An object larger than a megabyte is uploaded and reported whole.

        The content is not read back: the downlink of the bucket is billed, and
        the ETag plus the size of the object already prove what was written.
        """
        data = b'large object ' * (1 << 17)
        assert len(data) > (1 << 20)
        info = await put(probe, make_file, 'v1/large.pack', data)
        assert info.size == len(data)
        assert info.etag == hashlib.md5(data).hexdigest()
        head = await probe.head('v1/large.pack')
        assert head.size == len(data)
        assert head.etag == info.etag

    @pytest.mark.trio
    async def test_key_at_the_workspace_root(self, probe, make_file):
        """A key without a folder is the layout of the published contract.

        manifest.json sits at the root of the workspace
        ({Prefix}/{workspace}/manifest.json, see manifest.MANIFEST_FILE), not
        under a folder, and every other test of this module writes below v1/.
        A store that joined prefix and key with a '/' of its own would put it
        under a folder of an empty name.
        """
        data = b'{"schema": 1}'
        info = await put(probe, make_file, 'manifest.json', data)
        assert info.etag == hashlib.md5(data).hexdigest()
        assert await probe.get('manifest.json') == data
        assert sorted((await probe.list()).objects) == ['manifest.json']
        assert (await probe.head('manifest.json')).size == len(data)

    @pytest.mark.trio
    @pytest.mark.parametrize('segment', [
        'a#b.txt', 'a?b.txt', 'a%b.txt', 'a&b.txt', 'a=b.txt', 'a b.txt',
    ])
    async def test_key_with_a_reserved_character(self, probe, make_file, segment):
        """A key of reserved characters is stored under that very key.

        Unescaped, '#' would start a fragment, '?' a query and '%' would be
        decoded a second time; '&' and '=' would have to be read as separators
        of the canonical string. Each one is checked against the service, not
        only against the fake.
        """
        data = b'reserved'
        relative = 'v1/%s' % segment
        await put(probe, make_file, relative, data)
        assert await probe.get(relative) == data
        assert sorted((await probe.list()).objects) == [relative]

    @pytest.mark.trio
    async def test_a_prefix_needing_an_escape(self, request, make_file):
        """A workspace whose name has a space or a non-ascii character lists its objects.

        The prefix travels as a query parameter, and the query is the one place
        the store hands the encoding to yarl, which writes a space as '+'. If
        the service read that '+' as a plus instead of a space, the signature
        and the filter would both disagree and every object of the workspace
        would look missing (the uploader would resend everything, the cleaner
        would want to delete nothing or everything).
        """
        prefix = folder_of(request) + '中文 目录/'
        store = make_store(prefix)
        try:
            data = b'in a folder with a space'
            await put(store, make_file, 'v1/data.bin', data)
            assert await store.get('v1/data.bin') == data
            assert sorted((await store.list()).objects) == ['v1/data.bin']
        finally:
            try:
                await delete_folder(store)
            finally:
                await store.aclose()

    @pytest.mark.trio
    async def test_a_prefix_without_objects_lists_nothing(self):
        """A workspace that was never published is an empty listing, not an error.

        The first upload of a channel runs against a prefix that does not exist
        yet: the service answers an empty listing, and the uploader must read
        that as "every object is missing" instead of failing the run.
        """
        store = make_store('tests/never-published-%s/' % secrets.token_hex(4))
        try:
            listing = await store.list()
        finally:
            await store.aclose()
        assert listing.objects == {}

    @pytest.mark.trio
    async def test_concurrent_uploads(self, probe, make_file):
        """Four transfers run at once over one client, and all of them arrive.

        The uploader runs its transfers as trio tasks over one connection pool.
        The in-process fake of the offline tests cannot say whether the pool of
        a real TLS connection serves four requests at once; this can.
        """
        contents = {index: b'concurrent %d' % index for index in range(4)}
        paths = {
            index: make_file('concurrent-%d.bin' % index, data)
            for index, data in contents.items()
        }

        async def upload(index):
            data = contents[index]
            await probe.put(
                'v1/c%d.bin' % index, paths[index], len(data), hashlib.md5(data).hexdigest())

        async with trio.open_nursery() as nursery:
            for index in contents:
                nursery.start_soon(upload, index)
        assert sorted((await probe.list()).objects) == [
            'v1/c%d.bin' % index for index in contents]

    @pytest.mark.trio
    async def test_delete_is_idempotent(self, probe, make_file):
        """A key deleted twice, and a key that never existed, are successes."""
        await put(probe, make_file, 'v1/data.bin', b'data')
        await probe.delete('v1/data.bin')
        await probe.delete('v1/data.bin')
        await probe.delete('v1/never.bin')
        with pytest.raises(ObjectNotFound):
            await probe.get('v1/data.bin')

    @pytest.mark.trio
    @pytest.mark.parametrize('override', [
        {'secret_key': 'wrong-secret-of-the-test'},
        {'secret_id': 'AKIDwrongSecretIdOfTheTest'},
    ])
    async def test_a_wrong_credential_is_refused(self, request, override):
        """A wrong SecretKey or SecretId is refused with a signature error.

        The two halves fail with different codes (SignatureDoesNotMatch and
        InvalidAccessKeyId, measured) and the message must name the policy or
        the clock of the machine, never the credential it was given.
        """
        store = make_store(folder_of(request), **override)
        try:
            with pytest.raises(CosError) as caught:
                await store.list()
        finally:
            await store.aclose()
        assert caught.value.status == 403
        assert caught.value.code in (
            'SignatureDoesNotMatch', 'InvalidAccessKeyId', 'AccessDenied')
        for value in override.values():
            assert value not in str(caught.value)


class TestCosCapabilitiesReal:
    """The properties the uploader and the cleaner are built on."""

    @pytest.mark.trio
    async def test_etag_is_the_md5_of_a_single_put(self, probe, make_file):
        """A single PUT reports the MD5 of the content as its ETag.

        The uploader compares this ETag with the MD5 it holds of the local
        file, so a listing alone is enough to skip an unchanged object.
        """
        data = b'the etag of this object is its md5'
        info = await put(probe, make_file, 'v1/data.bin', data)
        expected = hashlib.md5(data).hexdigest()
        assert info.etag == expected
        assert (await probe.stat(['v1/data.bin']))['v1/data.bin'].etag == expected

    @pytest.mark.trio
    async def test_content_md5_is_enforced_by_the_server(self, probe, make_file):
        """A wrong Content-MD5 is refused and the object is not stored.

        This is the property the uploader relies on instead of reading every
        object back: the server compares the header with the bytes it received
        before it writes anything.
        """
        data = b'payload that is uploaded with a wrong digest'
        path = make_file('data.bin', data)
        with pytest.raises(CosError) as caught:
            await probe.put('v1/bad.bin', path, len(data), '0' * 32)
        assert caught.value.status == 400
        assert caught.value.code == 'BadDigest'
        assert await probe.stat(['v1/bad.bin']) == {}
        with pytest.raises(ObjectNotFound):
            await probe.get('v1/bad.bin')

    @pytest.mark.trio
    async def test_listing_reports_size_etag_and_time(self, probe, make_file):
        """A listing carries the three values the uploader and the cleaner read."""
        before = int(time.time())
        data = b'listed'
        await put(probe, make_file, 'v1/data.bin', data)
        listing = await probe.list()
        info = listing.objects['v1/data.bin']
        assert info.size == len(data)
        assert info.etag == hashlib.md5(data).hexdigest()
        assert before - 60 <= info.last_modified <= time.time() + 60
        # the clock of the cleaner comes from the server, not from this machine
        assert abs(listing.server_date - time.time()) < 300
        # ... and the age of the object is the difference of two values of that
        # one clock: if LastModified were in another zone, a manifest written a
        # second ago would read as hours old and the cleaner would delete the
        # generation that was just published
        assert 0 <= listing.server_date - info.last_modified <= 300

    @pytest.mark.trio
    async def test_object_headers_reach_the_bucket(self, probe, make_file):
        """Content-Type and Cache-Control are stored on the object.

        The uploader sets them per path (manifest.json is json and no-cache, a
        pack is an immutable octet stream, see the plan §6.6). A header the
        store forgets to send is a cache that serves a stale manifest for a
        year, so it is checked against the service and not only against the
        fake.
        """
        await put(
            probe, make_file, 'manifest.json', b'{}',
            content_type='application/json; charset=utf-8', cache_control='no-cache')
        # the metadata of the object is on the HEAD answer, ObjectInfo does not
        # carry it: the core flow never reads a Content-Type back
        response = await probe._call('HEAD', 'manifest.json')
        assert response.headers['Content-Type'] == 'application/json; charset=utf-8'
        assert response.headers['Cache-Control'] == 'no-cache'

    @pytest.mark.trio
    async def test_a_large_upload_never_reads_the_object_back(self, probe, make_file):
        """An upload costs one PUT and no GET at all.

        The downlink of the bucket is billed: a verification that read the
        object back would download every compiled wheel of every run, which is
        the cost the plan exists to avoid. The uploader rests on Content-MD5
        and the ETag instead, and this test is what fails the day a read-back
        is added.
        """
        calls = []
        original = probe._client.request

        async def record(method, url, **kwargs):
            calls.append((method, url))
            return await original(method, url, **kwargs)

        probe._client.request = record
        data = b'no read back ' * (1 << 17)
        assert len(data) > (1 << 20)
        await put(probe, make_file, 'v1/large.pack', data)
        assert [method for method, _url in calls] == ['PUT']

    @pytest.mark.trio
    async def test_overwrite_moves_last_modified_forward(self, probe, make_file):
        """A rewrite of the same content keeps the ETag and moves the time.

        The cleaner takes the age of an object from LastModified: a rewritten
        object must look young again, or a generation that was just published
        would be deleted on the next run.

        The two answers are not the same clock reading: the LastModified of a
        PUT answer is the Date of the response, which can be a second after the
        object was written (measured). So the mtime of the object is compared
        with HEAD and with the listing, not with the answer of the PUT.
        """
        data = b'same bytes'
        first = await put(probe, make_file, 'v1/data.bin', data)
        await trio.sleep(1.2)
        second = await put(probe, make_file, 'v1/data.bin', data)
        assert second.etag == first.etag
        assert second.last_modified > first.last_modified
        head = await probe.head('v1/data.bin')
        listed = (await probe.list()).objects['v1/data.bin']
        # HEAD and the listing report the moment of the write, and they agree
        assert head.last_modified == listed.last_modified
        # the write happened between the two responses
        assert first.last_modified <= head.last_modified <= second.last_modified

    @pytest.mark.trio
    async def test_prefix_isolation(self, probe, make_file):
        """The store never lists the objects of a sibling prefix.

        The sibling is named after the folder of this test plus a suffix, so
        the two prefixes share their text up to the last character but one.
        Without the trailing '/' of the prefix, the listing of the test folder
        would include the sibling.
        """
        sibling = make_store(probe.prefix.rstrip('/') + '-sibling/')
        try:
            await put(probe, make_file, 'v1/mine.bin', b'mine')
            await put(sibling, make_file, 'v1/theirs.bin', b'theirs')
            assert sorted((await probe.list()).objects) == ['v1/mine.bin']
            assert sorted((await sibling.list()).objects) == ['v1/theirs.bin']
        finally:
            try:
                await delete_folder(sibling)
            finally:
                await sibling.aclose()
