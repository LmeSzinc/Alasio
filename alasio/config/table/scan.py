from collections import deque

import msgspec

from alasio.base.timer import timer
from alasio.config.table.base import AlasioConfigDB, AlasioGuiDB
from alasio.config.table.key import AlasioKeyTable
from alasio.ext import env
from alasio.ext.path.atomic import atomic_read_bytes, atomic_remove, atomic_write
from alasio.ext.path.validate import validate_filename
from alasio.ext.pool import WORKER_POOL
from alasio.logger import logger

PROTECTED_NAMES = {'gui', 'template'}


class ConfigFile(msgspec.Struct):
    # config name
    name: str
    # mod name
    mod: str

    def read_mod_name(self):
        self.mod = AlasioKeyTable(self.name).mod_get()


def iter_local_files():
    """
    Scan all config files, except gui.db

    Yields:
        ConfigFile:
    """
    folder = env.PROJECT_ROOT / 'config'
    for file in folder.iter_files(ext='.db'):
        name = file.stem
        if name not in PROTECTED_NAMES:
            yield ConfigFile(name=name, mod='')


class ConfigInfo(msgspec.Struct):
    # config name
    name: str
    # mod name
    mod: str
    # scheduler group ID
    gid: int
    # instance ID in scheduler group
    iid: int
    # extra info in msgpack
    # b'\x80' is {} in msgpack
    extra: bytes = b'\x80'
    id: int = 0


class ConfigExtra(msgspec.Struct):
    # Run this instance once backend startup finished
    startup: bool = False


class ScanTable(AlasioGuiDB):
    TABLE_NAME = 'scan'
    PRIMARY_KEY = 'id'
    AUTO_INCREMENT = 'id'
    CREATE_TABLE = """
        CREATE TABLE "{TABLE_NAME}" (
        "id" INTEGER NOT NULL,
        "name" TEXT NOT NULL,
        "mod" TEXT NOT NULL,
        "gid" INTEGER NOT NULL,
        "iid" INTEGER NOT NULL,
        "extra" BLOB NOT NULL,
        PRIMARY KEY ("id"),
        UNIQUE ("name"),
        UNIQUE ("gid", "iid")
    );
    """
    MODEL = ConfigInfo

    @timer
    def scan(self):
        """
        Scan local files and maintain consistency with gui.db

        Returns:
            dict[str, ConfigInfo]:
        """
        job = WORKER_POOL.start_thread_soon(self.select, _orderby_=('gid', 'iid'))

        # local config files
        files = deque()
        with WORKER_POOL.wait_jobs() as pool:
            for row in iter_local_files():
                pool.start_thread_soon(row.read_mod_name)
                files.append(row)
        local: "dict[str, ConfigFile]" = {}
        for row in files:
            if row.mod:
                local[row.name] = row

        # configs in gui.db
        rows = job.get()
        record: "dict[str, ConfigInfo]" = {}
        for row in rows:
            record[row.name] = row

        with self.cursor(lazy=True) as c:
            # remove not exist
            list_record = list(record)
            for name in list_record:
                if name not in local:
                    # not exist
                    logger.info(f'Config disappear: {record[name]}')
                    self.delete_row(record[name], _cursor_=c)
                    record.pop(name, None)
                    continue
                row = record[name]
                if not row.mod:
                    # empty mod, this shouldn't happen
                    logger.info(f'Config has empty mod: {record[name]}')
                    self.delete_row(record[name], _cursor_=c)
                    record.pop(name, None)
                    continue
                local_row = local[name]
                if row.mod != local_row.mod:
                    # mod changed
                    logger.info(f'Config mod changed to {local_row.mod}: {record[name]}')
                    self.update_row(row, updates='mod', _cursor_=c)
                    row.mod = local_row.mod
                    continue

            # re-sort gid and iid
            gid = 0
            iid = 0
            for row in record.values():
                if gid == 0 or row.iid <= 0:
                    # new gid
                    if row.gid != gid or row.iid != iid:
                        row.gid = gid
                        row.iid = iid
                        logger.info(f'Config sorted: {row}')
                        self.update_row(row, updates=('gid', 'iid'), _cursor_=c)
                    gid += 1
                    iid = 0
                else:
                    # same gid, new iid
                    iid += 1
                    if row.gid != gid or row.iid != iid:
                        row.gid = gid
                        row.iid = iid
                        logger.info(f'Config sorted: {row}')
                        self.update_row(row, updates=('gid', 'iid'), _cursor_=c)

            # ensure new gid
            if iid > 0:
                gid += 1
                iid = 0

            # add new config
            for row in local.values():
                if row.name in record:
                    continue
                new = ConfigInfo(name=row.name, mod=row.mod, gid=gid, iid=iid)
                logger.info(f'Config appear: {new}')
                self.insert_row(new, _cursor_=c)
                gid += 1
                iid = 0
                record[row.name] = new

            # lazy commit
            c.commit()

        return record

    def config_add(self, name, mod):
        """
        Create a new config file.

        Args:
            name (str):
            mod (str):

        Raises:
            ValueError: If config name is invalid
            FileExistsError: If config file already exists
        """
        validate_filename(name)
        if name.lower() in PROTECTED_NAMES:
            raise ValueError(f'Config name is protected: {name}')

        file = AlasioConfigDB.config_file(name)
        if file.exists():
            raise FileExistsError

        # create
        table = AlasioKeyTable(name)
        table.mod_set(mod)

    def config_del(self, name):
        """
        Delete a config file.
        If file not exist, consider as success

        Args:
            name (str):

        Raises:
            ValueError: If config name is invalid
        """
        validate_filename(name)
        if name.lower() in PROTECTED_NAMES:
            raise ValueError(f'Config name is protected: {name}')

        # delete
        file = AlasioConfigDB.config_file(name)
        atomic_remove(file)

    def config_copy(self, source, target):
        """
        Copy an existing config as a new config

        Args:
            source (str):
            target (str):
        """
        validate_filename(source)
        if source.lower() in PROTECTED_NAMES:
            raise ValueError(f'Config name is protected: {source}')
        validate_filename(target)
        if target.lower() in PROTECTED_NAMES:
            raise ValueError(f'Config name is protected: {target}')

        source_file = AlasioConfigDB.config_file(source)
        target_file = AlasioConfigDB.config_file(target)
        if target_file.exists():
            raise FileExistsError(f'Target file to copy already exists: {target_file}')

        # copy
        try:
            content = atomic_read_bytes(source_file)
        except FileNotFoundError:
            raise FileExistsError(f'Source file to copy not found: {source_file}') from None
        atomic_write(target_file, content)
