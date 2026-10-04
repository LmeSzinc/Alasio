"""
The mirror set of an update server (ServerUrl) and the selected mirror
recorded in the shared gui.db (PackMirrorTable).

A mirror set is the normalized mirror structure of populate_mirrors()
(alasio/deploy/httpclient/probe.py):

    {
        'cn': {
            '123pan': 'https://123pan.example.com/alas',
            'tencent-cos': 'https://cos.example.com/alas',
        },
        'global': {'global': 'https://global.example.com/alas'},
    }

Groups are connectivity paths: ServerFile.probe probes them in
parallel, the fastest usable path wins. Members inside a group are
tried in their declared order, the first usable one wins: the order is
a preference (e.g. a free mirror before a metered one), not a measure
of connectivity. A group name is a label that tells the groups apart,
it carries no meaning; a mirror name is unique across the groups.

The name a probe selected is recorded in gui.db, keyed by the scope
(the identity of the update server) and fingerprinted by the mirror
structure (set_key): a record of another structure, or of a mirror the
set no longer has, is ignored. The record expires after a random
1~1.5 days (RECORD_TTL): an expired record is ignored and the next
flow probes again, so the selection follows the network environment
(a mirror that came back becomes the selection again, a better one
is picked up) within about a day, and the random moment spreads the
re-probes of a client fleet in time. The record is a hint of the
machine network environment, shared by every config of the
installation, and only an optimization: a broken database degrades
to a probe of every flow.
"""
import sqlite3
from hashlib import sha1
from time import time

import msgspec

from alasio.config.table.base import AlasioGuiDB
from alasio.deploy.httpclient.probe import Mirrors
from alasio.ext.cache import InstanceCacheOperation, cached_property
from alasio.logger import logger


class PackMirrorRow(msgspec.Struct):
    """
    The selected mirror of one update server in gui.db.
    """
    id: int = 0
    # identity of the update server, '' when there is only one
    scope: str = ''
    # fingerprint of the mirror structure the name was selected for
    set_key: str = ''
    # name of the mirror that worked last
    name: str = ''
    # expiration of the record, unix seconds (epoch, UTC based: a
    # timezone or a daylight saving shift never enters the
    # arithmetic): the writer sets it to now + a random RECORD_TTL, a
    # record at or before now is expired (the 0 of a row written by an
    # older version is always expired)
    expire: int = 0


class PackMirrorTable(AlasioGuiDB):
    """
    The selected mirror of an update server, one row per scope.
    """
    TABLE_NAME = 'pack_mirror'
    CREATE_TABLE = """
        CREATE TABLE "{TABLE_NAME}" (
        "id" INTEGER NOT NULL,
        "scope" TEXT NOT NULL,
        "set_key" TEXT NOT NULL,
        "name" TEXT NOT NULL,
        "expire" INTEGER NOT NULL,
        PRIMARY KEY ("id"),
        UNIQUE ("scope")
    );
    """
    MODEL = PackMirrorRow


class ServerUrl:
    """
    The mirror set of an update server and the selected mirror.

    mirrors is the Mirrors structure (see Mirrors.from_input()), the
    url of a mirror is read with url_of(); a mirror name is unique
    across the groups. The selected mirror name is recorded in gui.db
    (PackMirrorTable), a hint of the machine network environment: the
    probe of ServerFile overwrites it, a stale record is ignored, and a
    record expires after 1~1.5 days (RECORD_TTL) so the selection
    follows the network environment.

    A single mirror set has no selection to make: single, name and
    set_name() skip the database entirely and ServerFile probes the
    only mirror, so a single url behaves exactly as a plain base url.
    """

    # lifetime bounds of a record in seconds, (min, max): set_name()
    # writes expire = now + randint(*RECORD_TTL), the record expires
    # after 1~1.5 days and the next flow probes again. An expired
    # record is only a hint that is not an error (the update still
    # works), it makes the selection follow the network environment: a
    # mirror that came back becomes the selection again, a better one
    # is picked up. The random moment spreads the re-probes of a
    # client fleet in time, a fixed lifetime would make every client
    # probe at the same moment. The bounds are epoch seconds (UTC
    # based): a timezone or a daylight saving shift never enters the
    # arithmetic, only a system clock adjustment can (see name())
    RECORD_TTL = (86400, 129600)  # 1 ~ 1.5 days

    def __init__(self, mirrors, scope=''):
        """
        Args:
            mirrors (Mirrors | str | dict): The mirror input, see
                Mirrors.from_input() for the accepted forms
            scope (str, optional): Identity of this update server in
                the gui.db record, so independent mirror sets do not
                overwrite each other. Defaults to ''.
        """
        self.mirrors = Mirrors.from_input(mirrors)
        self.scope = scope

    @cached_property
    def single(self):
        """
        The only mirror name when the set has one mirror, '' otherwise.

        Returns:
            str: The only mirror name, or ''
        """
        if len(self.mirrors.urls) == 1:
            return next(iter(self.mirrors.urls))
        return ''

    @cached_property
    def set_key(self):
        """
        Fingerprint of the mirror structure: the group boundaries and
        the mirror names in their order. A record of another structure
        is ignored, so the mirror lists of an update can be edited and
        every client probes once again.

        The urls and the group names are not part of the fingerprint: a
        name is the identity of a mirror, its url is read from the
        configuration at every use, and a group name is a label only.
        The boundaries are part of it, moving a mirror to another group
        is a change of the selection semantics.

        Returns:
            str: sha1 hex digest of the mirror structure
        """
        structure = '\n'.join(','.join(members) for members in self.mirrors.groups.values())
        return sha1(structure.encode('utf-8')).hexdigest()

    @cached_property
    def _db(self):
        """
        The table of the gui.db record, created on first use.

        Returns:
            PackMirrorTable: The table
        """
        return PackMirrorTable()

    @cached_property
    def name(self):
        """
        The mirror name recorded in gui.db, the hint of the last
        selection; '' when there is no record, the record is stale or
        the record is expired.

        A record is stale when it was selected for another mirror
        structure (set_key mismatch, the set was edited) or names a
        mirror this set no longer has. A record is expired when its
        expiration (a random moment 1~1.5 days after the record was
        written, see set_name) is reached: it is treated as no record
        and the next flow probes again, so the selection follows the
        network environment. A record expiring further in the future
        than the max of RECORD_TTL cannot be written by this code - it
        is a tampered record or an abnormal clock - and is ignored too
        instead of pinning the machine. A single mirror set has no
        record.

        Returns:
            str: The recorded mirror name, '' when there is none
        """
        if self.single:
            return ''
        try:
            row = self._db.select_one(scope=self.scope)
        except sqlite3.Error as e:
            # the record is an optimization of the selection only, a
            # broken database must not break the update
            logger.warning(f'Failed to read the recorded mirror: {e}')
            return ''
        if row is None or row.set_key != self.set_key or row.name not in self.mirrors.urls:
            return ''
        now = int(time())
        if row.expire <= now:
            # the record is expired: select the mirror again, a mirror
            # that came back, or a better one, is picked up then
            logger.info(f'Mirror record of "{row.name}" is expired, selecting again')
            return ''
        if row.expire > now + self.RECORD_TTL[1]:
            # no version of this code writes a record that expires
            # further in the future: a tampered record or an abnormal
            # clock must not pin the machine, ignore the record
            logger.warning(
                f'Mirror record of "{row.name}" expires too far in the future, ignored: {row.expire}')
            return ''
        return row.name

    def set_name(self, name):
        """
        Record the mirror a probe selected, with an expiration of
        randint(*RECORD_TTL) seconds from now: the next flow probes
        again once it is reached (see the constants). An expired record
        is read as '' and cached as '', so a reselected mirror is
        rewritten with a fresh expiration even when the name is the
        same (the same-name skip does not apply to it). The write is
        skipped when the name is already the cached one (no IO). A
        broken database only warns: the update works without the
        record, it just probes every flow.

        Args:
            name (str): Mirror name selected by the probe

        Raises:
            ValueError: If the name is not in this mirror set
        """
        if self.single:
            return
        if name not in self.mirrors.urls:
            raise ValueError(f'Unknown mirror: {name!r}')
        if InstanceCacheOperation.get(self, 'name', '') == name:
            return

        # a local import: only the write path needs the random module
        from random import randint
        expire = int(time()) + randint(*self.RECORD_TTL)
        try:
            self._db.upsert_row(
                PackMirrorRow(scope=self.scope, set_key=self.set_key, name=name, expire=expire),
                conflicts='scope', updates=('set_key', 'name', 'expire'),
            )
        except sqlite3.Error as e:
            logger.warning(f'Failed to record the mirror: {e}')
            return
        # keep the cached value in sync, this instance never reads the
        # database again
        InstanceCacheOperation.set(self, 'name', name)

    def url_of(self, name):
        """
        Base url of a mirror name of this set.

        Args:
            name (str): Mirror name

        Returns:
            str: The base url, '' when the name is not in this set
        """
        return self.mirrors.url_of(name)
