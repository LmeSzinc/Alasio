import sqlite3
from typing import Type, TypeVar

import msgspec

from alasio.db.conn import SQLITE_POOL, SqlitePoolCursor
from alasio.ext.cache import cached_property

T_model = TypeVar('T_model', bound=msgspec.Struct)


class AlasioTableError(Exception):
    """
    Raised when subclass of AlasioTable is not defined property
    """
    pass


def iter_class_field(cls):
    """
    A generator that iterates over all type-annotated class variables
    of a class and its parents, with special support for msgspec.
    A simpler version of `typing.get_type_hints`, that won't do _eval_type() just leave annotation as it is

    Args:
        cls (type): A class

    Yields:
        tuple[str, Any, Any]: field name, annotation, value
            if field has no default, default=msgspec.NODEFAULT
    """
    for base in reversed(cls.__mro__):
        annotations = base.__dict__.get('__annotations__', {})
        for name, anno in annotations.items():
            # get value
            value = getattr(cls, name, msgspec.NODEFAULT)
            # ignore UNSET
            if value == msgspec.UNSET:
                continue
            yield name, anno, value


class AlasioTable:
    # Tables should override these
    TABLE_NAME = ''
    # Name of PRIMARY_KEY field
    PRIMARY_KEY = ''
    # Name of AUTO_INCREMENT field
    # In most databases you can only have one AUTO_INCREMENT field
    AUTO_INCREMENT = ''
    # SQL to create the table
    # when executing any sql, table will be auto created
    # Example:
    #     CREATE TABLE "{TABLE_NAME}" (
    #         "id" INTEGER NOT NULL,
    #         "task" TEXT NOT NULL,
    #         "group" TEXT NOT NULL,
    #         "value" BLOB NOT NULL,
    #         PRIMARY KEY ("id"),
    #         UNIQUE ("task", "group")
    #     )
    # If CREATE_TABLE is f-string, CREATE_TABLE is a static SQL
    # If CREATE_TABLE is normal string, CREATE_TABLE will fill in {TABLE_NAME} dynamically,
    #   so you can operate multiple tables that have the same schema
    CREATE_TABLE = ''
    # msgspec model of the table row
    # You need to ensure MODEL matches the schema in CREATE_TABLE
    MODEL: Type[T_model]

    def __init__(self, file: str):
        """
        Args:
            file: Absolute filepath to database
                or :memory:
        """
        self.file = file

    def cursor(self):
        """
        Create a cursor to operate sqlite

        Returns:
            SqlitePoolCursor:
        """
        cursor = SQLITE_POOL.cursor(self.file)
        # copy CREATE_TABLE to cursor, so it can auto create table if table not exists
        cursor.TABLE_NAME = self.TABLE_NAME
        cursor.CREATE_TABLE = self.CREATE_TABLE
        return cursor

    def create_table(self):
        """
        Table is auto created, but you can still create it manually.

        Returns:
            bool: If created
        """
        if not self.CREATE_TABLE:
            AlasioTableError(f'AlasioTable {self.__class__.__name__} has no CREATE_TABLE defined')
        try:
            with self.cursor() as c:
                c.execute(self.CREATE_TABLE)
            return True
        except sqlite3.OperationalError as e:
            if 'already exists' in str(e):
                return False
            else:
                raise

    def drop_table(self):
        """
        Drop current table
        """
        if not self.TABLE_NAME:
            AlasioTableError(f'AlasioTable {self.__class__.__name__} has no TABLE_NAME defined')
        with self.cursor() as c:
            c.execute(f'DROP TABLE IF EXISTS "{self.TABLE_NAME}"')

    @staticmethod
    def sql_select_kwargs_to_condition(kwargs):
        """
        Convert **kwarg query to sql query condition.
        To avoid SQL injection, VALUES are variables that reference the key

        Returns:
            str: "task"=:task AND "group"=:group AND ...
        """
        conditions = [f'"{k}"=:{k}' for k in kwargs.keys()]
        conditions = ' AND '.join(conditions)
        return conditions

    def select(self, _cursor_=None, **kwargs) -> "list[T_model]":
        """
        Query table in a pythonic way
        Equivalent to:
            SELECT * FROM {table_name} WHERE key1=value1 AND ...

        Examples:
            rows = table.select(task='Main')
        """
        if kwargs:
            conditions = self.sql_select_kwargs_to_condition(kwargs)
            sql = f'SELECT * FROM "{self.TABLE_NAME}" WHERE {conditions}'
            with self.cursor() as c:
                c.execute(sql, kwargs)
                result = c.fetchall()
        else:
            sql = f'SELECT * FROM "{self.TABLE_NAME}"'
            with self.cursor() as c:
                c.execute(sql)
                result = c.fetchall()

        # convert to msgspec model
        result = [self.MODEL(*row) for row in result]
        return result

    def select_first(self, _cursor_: "SqlitePoolCursor | None" = None, **kwargs) -> "T_model | None":
        """
        Query table in a pythonic way
        Equivalent to:
            SELECT * FROM {table_name} WHERE key1=value1 AND ... LIMIT 1

        Examples:
            rows = table.select_first(value=1)
        """
        if kwargs:
            conditions = self.sql_select_kwargs_to_condition(kwargs)
            sql = f'SELECT * FROM "{self.TABLE_NAME}" WHERE {conditions} LIMIT 1'
            if _cursor_ is None:
                with self.cursor() as c:
                    c.execute(sql, kwargs)
                    result = c.fetchone()
            else:
                _cursor_.execute(sql, kwargs)
                result = _cursor_.fetchone()
        else:
            sql = f'SELECT * FROM "{self.TABLE_NAME}" LIMIT 1'
            if _cursor_ is None:
                with self.cursor() as c:
                    c.execute(sql)
                    result = c.fetchone()
            else:
                _cursor_.execute(sql)
                result = _cursor_.fetchone()

        # convert to msgspec model
        if result is not None:
            result = self.MODEL(*result)
        return result

    @cached_property
    def field_names(self):
        """
        Returns:
            list[str]: All field names of model
        """
        try:
            model = self.MODEL
        except AttributeError:
            raise AlasioTableError(f'AlasioTable {self.__class__.__name__} has no MODEL defined')
        return [name for name, _, _ in iter_class_field(model)]

    @cached_property
    def sql_insert_columns_placeholders(self):
        """
        Generate sql column, so you can use them in
            INSERT INTO {table_name} ({columns}) VALUES ({placeholders})
        AUTO_INCREMENT field will be ignored.

        Returns:
            str: "task","group",...
        """
        fields = self.field_names
        # get non auto increment fields
        if not self.AUTO_INCREMENT:
            raise AlasioTableError(f'AlasioTable {self.__class__.__name__} has no AUTO_INCREMENT defined')
        columns = [name for name in fields if name != self.AUTO_INCREMENT]
        # :task,:group,...
        placeholders = ','.join([f':{k}' for k in columns])
        # "task","group",...
        columns = ",".join([f'"{k}"' for k in columns])
        return columns, placeholders

    def insert(
            self,
            rows: "T_model | list[T_model]",
            _cursor_: "SqlitePoolCursor | None" = None
    ):
        """
        Insert one row of data or a list of data, with auto commit

        You can also input an existing cursor to manage transaction yourself
        with table.cursor() as cursor:
            cursor.insert(row, _cursor_=cursor)
            cursor.insert([row2, row3], _cursor_=cursor)
            # don't forget to commit
            cursor.commit()
        """
        columns, placeholders = self.sql_insert_columns_placeholders
        sql = f'INSERT INTO {self.TABLE_NAME} ({columns}) VALUES ({placeholders})'

        if isinstance(rows, list):
            rows = [msgspec.structs.asdict(row) for row in rows]
            if _cursor_ is None:
                with self.cursor() as c:
                    c.executemany(sql, rows)
                    c.commit()
            else:
                _cursor_.executemany(sql, rows)
        else:
            rows = msgspec.structs.asdict(rows)
            if _cursor_ is None:
                with self.cursor() as c:
                    c.execute(sql, rows)
                    c.commit()
            else:
                _cursor_.execute(sql, rows)

    def update(
            self,
            rows: "T_model | list[T_model]",
            fields: "str | list[str]" = '',
            _cursor_: "SqlitePoolCursor | None" = None
    ):
        pass
