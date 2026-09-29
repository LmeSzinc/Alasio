"""
Tests for GitObjectManager.cat() on several threads.

The object dict of one repo is shared by the threads of the pool of
reset_validate_files() and of the pack builds, so cat() must give every thread
the very same object the serial run gives, and every object it publishes must
be finished: a reader outside of cat() never triggers a decode of a shared
object. See doc/2026-09-30_git-cat-thread-safety.md.
"""

import random
import threading
from hashlib import sha1

import pytest

from alasio.ext import env
from alasio.git.obj.obj import parse_objtype
from alasio.git.repo import GitRepo
from alasio.git.stage.hashobj import blob_hash


def new_repo():
    """
    A repo with a cold object cache, the state the pool finds it in

    Returns:
        GitRepo:
    """
    return GitRepo(str(env.ALASIO_ROOT)).read_lazy()


def cold_objects(repo):
    """
    Every object of the cold cache with its raw object type, 0 when the type is
    only known after reading (loose object, or pack object skipped by read_lazy)

    Args:
        repo (GitRepo):

    Returns:
        dict[str, int]: sha1 -> raw object type
    """
    out = {sha1_: parse_objtype(data) for sha1_, data in repo.dict_object_data.items()}
    out.update(dict.fromkeys(repo.dict_object_unread, 0))
    return out


def sample_objects(objects, count):
    """
    Half delta objects and half the rest, so the delta solve, the pack read and
    the loose object paths are all in the sample

    Args:
        objects (dict[str, int]): sha1 -> raw object type
        count (int):

    Returns:
        list[str]:
    """
    random_ = random.Random(0)
    deltas = [sha1_ for sha1_, objtype in objects.items() if objtype in (6, 7)]
    random_.shuffle(deltas)
    deltas = deltas[:count // 2]
    rest = [sha1_ for sha1_, objtype in objects.items() if objtype not in (6, 7)]
    random_.shuffle(rest)
    shas = deltas + rest[:count - len(deltas)]
    shas.sort()
    return shas


def signature(obj):
    """
    What cat() promises for an object: resolved type and decoded content

    Args:
        obj (GitObject | GitLooseObject):

    Returns:
        tuple:
    """
    decoded = obj.decoded
    if isinstance(decoded, bytes):
        return obj.type, len(decoded), sha1(decoded).hexdigest()
    return obj.type, repr(decoded)


class TestCatPublish:
    """The object dict only ever holds finished objects."""

    COUNT = 300

    def test_published_object_is_finished(self):
        """
        decoded is computed before an object enters dict_object, and a solved
        object replaces its delta: the dict holds no half built object and no
        unresolved delta.
        """
        repo = new_repo()
        shas = sample_objects(cold_objects(repo), self.COUNT)
        assert len(shas) == self.COUNT

        for sha1_ in shas:
            repo.cat(sha1_)
            obj = repo.dict_object[sha1_]
            # 'decoded' in the instance dict: the descriptor never runs again,
            # a reader of the object dict never writes to a shared object
            assert 'decoded' in obj.__dict__
            assert obj.type in (1, 2, 3, 4)
            if obj.type == 3:
                # a blob holds its plain content, it hashes back to its own sha1
                # data of a pack blob is a memoryview, data of a loose one is bytes
                assert bytes(obj.data) == obj.decoded
                assert blob_hash(obj.decoded) == sha1_


class TestCatConcurrent:
    """cat() gives every thread the serial result, on a cold object cache."""

    COUNT = 1000
    THREADS = 8

    def test_concurrent_cat_matches_serial(self):
        """
        Every thread resolves the same cold objects, the shape of two pack
        builds that share one repo: no thread may raise, and every thread must
        get exactly the object of the serial run.

        Before the fix this raised AttributeError and ObjectBroken, and a tree
        could even be read as its delta instructions, see the doc.
        """
        repo = new_repo()
        shas = sample_objects(cold_objects(repo), self.COUNT)
        assert len(shas) > self.COUNT // 2

        # serial reference, on the same cold objects
        reference = {sha1_: signature(repo.cat(sha1_)) for sha1_ in shas}

        # cold cache again, now for the threads
        repo = new_repo()
        barrier = threading.Barrier(self.THREADS)
        results = [None] * self.THREADS
        errors = []

        def work(index):
            out = {}
            barrier.wait()
            for sha1_ in shas:
                try:
                    out[sha1_] = signature(repo.cat(sha1_))
                except Exception as e:
                    errors.append((sha1_, repr(e)))
            results[index] = out

        list_thread = [threading.Thread(target=work, args=(index,)) for index in range(self.THREADS)]
        for thread in list_thread:
            thread.start()
        for thread in list_thread:
            thread.join()

        assert errors == []
        for out in results:
            assert out == reference


class TestCatStaleCacheMiss:
    """A cache miss that another thread already answered is not an error."""

    def test_object_built_while_the_caller_looked_up_is_returned(self, monkeypatch):
        """
        The caller misses dict_object, another thread builds the object and
        consumes both of its sources, the caller must get the object instead of
        KeyError: No such object.
        """
        repo = new_repo()
        sha1_ = sample_objects(cold_objects(repo), 1)[0]
        repo.cat(sha1_)

        class MissOnce(dict):
            """The first lookup misses, as if the thread was preempted"""

            def __init__(self, data):
                super().__init__(data)
                self.armed = True

            def get(self, key, default=None):
                if self.armed:
                    self.armed = False
                    return default
                return super().get(key, default)

        monkeypatch.setattr(repo, 'dict_object', MissOnce(repo.dict_object))
        obj = repo.cat_shallow(sha1_)
        assert obj.type in (1, 2, 3, 4)

    def test_unknown_sha1_raises_key_error(self):
        """An object that does not exist at all still raises KeyError."""
        repo = new_repo()
        with pytest.raises(KeyError, match='No such object'):
            repo.cat('0' * 40)
