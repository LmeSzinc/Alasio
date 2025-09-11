from msgspec import Struct

from alasio.config.table.base import AlasioConfigDB


class ConfigRow(Struct):
    task: str
    group: str
    # json in msgpack
    value: bytes
    # PRIMARY_KEY, AUTO_INCREMENT
    id: int = 0


class AlasioConfigTable(AlasioConfigDB):
    TABLE_NAME = 'config'
    PRIMARY_KEY = 'id'
    AUTO_INCREMENT = 'id'
    CREATE_TABLE = """
        CREATE TABLE "{TABLE_NAME}" (
        "id" INTEGER NOT NULL,
        "task" TEXT NOT NULL,
        "group" TEXT NOT NULL,
        "value" BLOB NOT NULL,
        PRIMARY KEY ("id"),
        UNIQUE ("task", "group")
    );
    """
    MODEL = ConfigRow

    def read_task_rows(self, tasks, groups):
        """
        Read tasks groups in config
        Note that the result might not match the given tasks group
        - might be missing (user config same as default)
        - might have unneeded (old groups that not being used anymore)

        Args:
            tasks (list[str]): List of task
            groups (list[tuple[str, str]]): List of (task, group)

        Returns:
            list[ConfigRow]:
        """
        conditions = []
        params = []
        if tasks:
            if len(tasks) == 1:
                # "task"="GemsFarming"
                conditions.append(f'"task"=?')
                params.append(tasks[0])
            else:
                # "task" IN ("Alas","General","GemsFarming")
                ts = ','.join(['?' for _ in tasks])
                conditions.append(f'"task" in ({ts})')
                params += tasks
        for t, g in groups:
            # ("task"="Main" AND "group"="Emotion")
            conditions.append(f'("task"=? AND "group"=?)')
            # Unpack and repack to ensure it's a [<task>, <group>]
            params.append(t)
            params.append(g)

        # SELECT * FROM "config" WHERE
        # "task" IN ("Alas", "General", "GemsFarming")
        # OR ("task"="Main" AND "group"="Emotion")
        # OR ("task"="Alas" AND "group"="EmulatorInfo")
        conditions = ' OR '.join(conditions)
        sql = f'SELECT * FROM "{self.TABLE_NAME}" WHERE {conditions}'

        # query
        rows = self.select_by_sql(sql, params)
        return rows

    def read_rows(self, events, _cursor_=None):
        """
        Args:
            events (list): List of objects that have attribute "task" and "group"
            _cursor_:

        Returns:
            list[ConfigRow]:
        """
        conditions = []
        params = []
        for event in events:
            # ("task"="Main" AND "group"="Emotion")
            conditions.append(f'("task"=? AND "group"=?)')
            # Unpack and repack to ensure it's a [<task>, <group>]
            params.append(event.task)
            params.append(event.group)

        conditions = ' OR '.join(conditions)
        sql = f'SELECT * FROM "{self.TABLE_NAME}" WHERE {conditions}'

        # query
        rows: "list[ConfigRow]" = self.select_by_sql(sql, params, _cursor_=_cursor_)
        return rows
