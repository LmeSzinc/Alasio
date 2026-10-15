"""
The selection record of the probes, in the shared gui.db (MirrorRecordTable).

The record is the hint of the machine network environment: which mirror of a
candidate set served the last probe, and when the hint expires. It is keyed by
(scene, name): the scene is the usage domain of the candidate set ('repo',
'pypi', 'dns', ...), the name is the identity of the set inside the scene (the
DeployJob(name=...) key of the pack channels, '' for a set of its own).

    load_result('repo', 'alas', mirrors)     the mirror recorded for the set, '' when
                                             there is no usable record
    save_result('repo', 'alas', mirrors, 'tencent-cos')   record a winner

The record is only a hint, never a correctness dependency: a missing, stale or
expired record only makes the next probe run (a few requests), the update works
without it. It is written after a probe selected a mirror (see the flavors) and
only while digest matches the mirror structure: editing the mirror list of a
deployment invalidates the record of every client, so the edit takes effect.

A record expires after a random 1~1.5 days (RECORD_TTL): an expired record is
read as '' and the next flow probes again, so the selection follows the network
environment (a mirror that came back becomes the selection again, a better one
is picked up) within about a day, and the random moment spreads the re-probes
of a client fleet in time. The record stores the name only, the url is always
read from the configuration: a tampered database cannot point a request at an
arbitrary host, it can only make a probe run.

The functions are synchronous (sqlite3 is), the flavors call them directly
(sync) or through trio.to_thread.run_sync (async). Every failure of the
database degrades: a read fails to '' and a write only logs a warning, the
probe of the flow continues without the hint. No transaction spans a probe and
no lock protects the row: the write is a single UPSERT (the SQLite write lock
lives microseconds), two processes writing at once end with the last winner,
and both winners are mirrors that were usable (the write is called after a
probe). The in-process deduplication of the probes is the probe_lock of the
flavors, not a database lock.
"""
import sqlite3
from random import randint
from time import time

import msgspec

from alasio.config.table.base import AlasioGuiDB
from alasio.logger import logger

# lifetime bounds of a record in seconds, (min, max): save_result() writes
# expire = now + randint(*RECORD_TTL), the record expires after 1~1.5 days and
# the next flow probes again. An expired record is only a hint that is not an
# error (the update still works), it makes the selection follow the network
# environment: a mirror that came back becomes the selection again, a better
# one is picked up. The random moment spreads the re-probes of a client fleet
# in time, a fixed lifetime would make every client probe at the same moment.
# The bounds are epoch seconds (UTC based): a timezone or a daylight saving
# shift never enters the arithmetic, only a system clock adjustment can (see
# load_result)
RECORD_TTL = (86400, 129600)  # 1 ~ 1.5 days


class MirrorRecord(msgspec.Struct):
    """
    The selected mirror of one candidate set in gui.db.
    """
    id: int = 0
    # usage domain of the candidate set: 'repo', 'pypi', 'dns', ...
    scene: str = ''
    # identity of the set inside the scene, the DeployJob(name=...) key of the
    # pack channels, '' for a set of its own
    name: str = ''
    # fingerprint of the mirror structure the result was selected for
    digest: str = ''
    # name of the mirror that worked last
    result: str = ''
    # expiration of the record, unix seconds (epoch, UTC based: a timezone or a
    # daylight saving shift never enters the arithmetic): the writer sets it to
    # now + a random RECORD_TTL, a record at or before now is expired (the 0 of
    # a row written by an older version is always expired)
    expire: int = 0


class MirrorRecordTable(AlasioGuiDB):
    """
    The selected mirror of one candidate set, one row per (scene, name).
    """
    TABLE_NAME = 'mirror_record'
    CREATE_TABLE = """
        CREATE TABLE "{TABLE_NAME}" (
        "id" INTEGER NOT NULL,
        "scene" TEXT NOT NULL,
        "name" TEXT NOT NULL,
        "digest" TEXT NOT NULL,
        "result" TEXT NOT NULL,
        "expire" INTEGER NOT NULL,
        PRIMARY KEY ("id"),
        UNIQUE ("scene", "name")
    );
    """
    MODEL = MirrorRecord


def _table(table=None):
    """
    The record table of a call: the injected one, or the gui.db table.

    Args:
        table (AlasioGuiDB-like, optional): Injected table, e.g. the
            in-memory table of the tests. Defaults to None, the real
            MirrorRecordTable (a singleton, created on first use)

    Returns:
        MirrorRecordTable: The table to read or write
    """
    return MirrorRecordTable() if table is None else table


def load_result(scene, name, mirrors, table=None):
    """
    The mirror this candidate set last selected, '' when there is no
    usable record.

    A record is usable when it is not stale and not expired:

    - a record of another mirror structure (digest mismatch, the set
      was edited) or of a mirror this set no longer has is ignored;
    - a record past its expiration is ignored: the caller probes again
      and, when the same mirror wins, writes a fresh record;
    - a record expiring further in the future than the max of
      RECORD_TTL cannot be written by this code - a tampered record or
      an abnormal clock - and is ignored too instead of pinning the
      machine;
    - a single mirror set has no record: there is no selection to
      remember, the reader skips the database.

    The read is blocking sqlite IO, the async flavors call it through a
    worker thread. A failure of the database degrades to '' with a
    warning: the record is an optimization of the selection only.

    Args:
        scene (str): Usage domain of the candidate set, e.g. 'repo'
        name (str): Identity of the set inside the scene, '' allowed
        mirrors (Mirrors): Candidate structure the record is checked
            against (its digest and its names)
        table (AlasioGuiDB-like, optional): Injected table for the
            tests. Defaults to None, the real MirrorRecordTable

    Returns:
        str: The recorded mirror name, '' when the caller has to probe
    """
    if mirrors.single:
        return ''
    try:
        row = _table(table).select_one(scene=scene, name=name)
    except sqlite3.Error as e:
        # the record is an optimization of the selection only, a
        # broken database must not break the update
        logger.warning(f'Failed to read the mirror record of {scene}/{name}: {e}')
        return ''
    if row is None or row.digest != mirrors.digest or row.result not in mirrors.urls:
        return ''
    now = int(time())
    if row.expire <= now:
        # the record is expired: select the mirror again, a mirror
        # that came back, or a better one, is picked up then
        logger.info(f'Mirror record of {scene}/{name} is expired, selecting again: {row.result}')
        return ''
    if row.expire > now + RECORD_TTL[1]:
        # no version of this code writes a record that expires
        # further in the future: a tampered record or an abnormal
        # clock must not pin the machine, ignore the record
        logger.warning(
            f'Mirror record of {scene}/{name} expires too far in the future, ignored: '
            f'{row.result}, expire {row.expire}')
        return ''
    return row.result


def save_result(scene, name, mirrors, result, table=None):
    """
    Record the mirror a probe selected, with an expiration of
    randint(*RECORD_TTL) seconds from now: the next flow probes again
    once it is reached (see the constants).

    The write is a single UPSERT of the (scene, name) row: it cannot
    lose another column and needs no lock, concurrent writers end with
    the last winner (every winner was usable, see the module note). A
    single mirror set is not recorded (no selection to remember) and a
    name outside the set is a caller bug.

    The write is blocking sqlite IO, the async flavors call it through
    a worker thread. A failure of the database only logs a warning:
    the update works without the record, it just probes every flow.

    Args:
        scene (str): Usage domain of the candidate set, e.g. 'repo'
        name (str): Identity of the set inside the scene, '' allowed
        mirrors (Mirrors): Candidate structure the result belongs to
        result (str): Mirror name selected by the probe
        table (AlasioGuiDB-like, optional): Injected table for the
            tests. Defaults to None, the real MirrorRecordTable

    Raises:
        ValueError: If the result is not a mirror of the structure
    """
    if mirrors.single:
        return
    if result not in mirrors.urls:
        raise ValueError(f'Unknown mirror: {result!r}')
    expire = int(time()) + randint(*RECORD_TTL)
    try:
        _table(table).upsert_row(
            MirrorRecord(
                scene=scene, name=name, digest=mirrors.digest, result=result, expire=expire),
            conflicts=('scene', 'name'), updates=('digest', 'result', 'expire'),
        )
    except sqlite3.Error as e:
        logger.warning(f'Failed to record the mirror of {scene}/{name}: {e}')
