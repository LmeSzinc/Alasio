from collections import deque
from copy import deepcopy
from typing import Union

import msgspec

from alasio.config.table.base import AlasioConfigDB, AlasioGuiDB
from alasio.config.table.key import AlasioKeyTable
from alasio.ext import env
from alasio.ext.path.atomic import atomic_read_bytes, atomic_remove, atomic_write
from alasio.ext.path.validate import validate_filename
from alasio.ext.pool import WORKER_POOL
from alasio.ext.reactive.event import RpcValueError
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
        try:
            validate_filename(file.name)
        except ValueError:
            continue
        name = file.stem
        if name not in PROTECTED_NAMES:
            yield ConfigFile(name=name, mod='')


class DndRequest(msgspec.Struct):
    name: str
    gid: Union[float, int]
    iid: Union[float, int]


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

    @staticmethod
    def resort_configs(configs):
        """
        Re-sorts a dictionary of ConfigInfo objects in-place.

        This function sorts the values of the input dictionary based on their current gid/iid
        and then assigns new, clean, 1-based, continuous integer gid/iid values directly to the objects.

        Args:
            configs (dict[str, ConfigInfo]):
                key: config name, value: ConfigInfo object
        """
        sorted_rows = sorted(configs.values(), key=lambda r: (r.gid, r.iid))

        new_gid = 0
        new_iid = 0
        last_gid = float('-inf')

        for row in sorted_rows:
            if row.gid > last_gid:
                # row request itself a new gid
                new_gid += 1
                new_iid = 1
            else:
                # row in the same group, increase iid
                new_iid += 1

            last_gid = row.gid

            # In-place modification
            row.gid = new_gid
            row.iid = new_iid

    def update_with_conflict_resolution(self, old_configs, new_configs, _cursor_):
        """
        Updates the database from an old state to a new state, resolving conflicts.
        old_configs and new_configs should have rows with the same config names.

        Args:
            old_configs (dict[str, ConfigInfo]):
            new_configs (dict[str, ConfigInfo]):
            _cursor_:
        """
        pending_updates = []
        current_positions = {(row.gid, row.iid) for row in old_configs.values()}

        # iter updates
        for name, new_row in new_configs.items():
            try:
                old_row = old_configs[name]
            except KeyError:
                # this should happen
                continue
            if old_row.gid != new_row.gid or old_row.iid != new_row.iid:
                # logger.info(f"Planning update for '{name}': "
                #             f"old=({old_row.gid}, {old_row.iid}), new=({new_row.gid}, {new_row.iid})")
                pending_updates.append({
                    'row': new_row,
                    'old_pos': (old_row.gid, old_row.iid),
                    'new_pos': (new_row.gid, new_row.iid)
                })

        # resolve update conflicts
        # database check UNIQUE(gid, iid) on each sql call, so you can't just do UPDATE literally
        updated_in_last_pass = True
        while pending_updates and updated_in_last_pass:
            updated_in_last_pass = False
            remaining_updates = []

            for update in pending_updates:
                target_pos = update['new_pos']

                # check if target (gid, iid) is available
                if target_pos not in current_positions:
                    # we are safe
                    row = update['row']
                    old_pos = update['old_pos']

                    row.gid, row.iid = target_pos
                    self.update_row(row, updates=('gid', 'iid'), _cursor_=_cursor_)

                    current_positions.remove(old_pos)
                    current_positions.add(target_pos)
                    updated_in_last_pass = True
                    logger.info(f'Resolved and updated "{row.name}" from {old_pos} to {target_pos}')
                else:
                    # target already in use, skip and check in the next pass
                    remaining_updates.append(update)

            pending_updates = remaining_updates

        # Check deadlock
        if pending_updates:
            # A->B, B->A shouldn't happen
            def pretty(u):
                config_name = u['row'].name
                return f'{config_name} from {u["old_pos"]} to {u["new_pos"]}'

            unresolved = ', '.join([pretty(u) for u in pending_updates])
            error_message = f"Detected an unresolvable circular dependency in gid/iid update: {unresolved}"
            logger.error(error_message)
            raise RuntimeError(error_message)

    def select_rows(self, _cursor_=None):
        """
        Returns:
            dict[str, ConfigInfo]:
        """
        rows = self.select(_orderby_=('gid', 'iid'), _cursor_=_cursor_)
        record: "dict[str, ConfigInfo]" = {}
        for row in rows:
            record[row.name] = row
        return record

    def scan(self):
        """
        Scan local files and maintain consistency with gui.db

        Returns:
            dict[str, ConfigInfo]:
        """
        job = WORKER_POOL.start_thread_soon(self.select_rows)

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
        record = job.get()

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
            new_record = deepcopy(record)
            self.resort_configs(new_record)
            self.update_with_conflict_resolution(old_configs=record, new_configs=new_record, _cursor_=c)
            record = new_record

            # starts from maximum gid
            gid = 0
            for row in record.values():
                if row.gid > gid:
                    gid = row.gid

            # add new config
            for row in local.values():
                if row.name in record:
                    continue
                gid += 1
                iid = 1
                new = ConfigInfo(name=row.name, mod=row.mod, gid=gid, iid=iid)
                logger.info(f'Config appear: {new}')
                self.insert_row(new, _cursor_=c)
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
            raise RpcValueError(f'Config name to add is protected: {name}')

        file = AlasioConfigDB.config_file(name)
        if file.exists():
            raise RpcValueError(f'Config file to add already exists {file}')

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
            raise RpcValueError(f'Config name is protected: {name}')

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
            raise RpcValueError(f'Source config name is protected: {source}')
        validate_filename(target)
        if target.lower() in PROTECTED_NAMES:
            raise RpcValueError(f'Target config name is protected: {target}')

        source_file = AlasioConfigDB.config_file(source)
        target_file = AlasioConfigDB.config_file(target)
        if target_file.exists():
            raise RpcValueError(f'Target file to copy already exists: {target_file}')

        # copy
        try:
            content = atomic_read_bytes(source_file)
        except FileNotFoundError:
            raise RpcValueError(f'Source file to copy not found: {source_file}') from None
        atomic_write(target_file, content)

    def config_dnd(self, requests):
        """
        Handles drag-and-drop sorting requests from the frontend.

        Let's say we have:
        A: (gid=1, iid=1)
        B: (gid=2, iid=1)
        C: (gid=3, iid=1)
        If you request C: (gid=1.99, iid=1)
            C will be inserted between A and B, and hava a standalone group, gid=2,
            B will be pushed to gid=3
        If you request C: (gid=2, iid=0.99)
            C will be inserted in to B's group (gid=2) and before B, iid=1
            B will be pushed to iid=2
        If you request C: (gid=2, iid=1.99)
            C will be inserted in to B's group (gid=2) and after B, iid=2

        Args:
            requests (list[DndRequest]):
        """
        # No lazy cursor, since we select first
        with self.cursor() as c:
            record = self.select_rows(_cursor_=c)
            if not record:
                return
            new_record = deepcopy(record)

            # apply request to temp configs
            for req in requests:
                try:
                    row = new_record[req.name]
                except KeyError:
                    # trying to dnd a non-exist row
                    raise RpcValueError(f'Config name "{req.name}" does not exist, cannot perform DND') from None
                row.gid = req.gid
                row.iid = req.iid

            self.resort_configs(new_record)
            self.update_with_conflict_resolution(old_configs=record, new_configs=new_record, _cursor_=c)
