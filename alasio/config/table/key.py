import sqlite3
from typing import Type, TypeVar

import msgspec

from alasio.config.table.base import AlasioConfigDB
from alasio.ext.cache import cached_property
from alasio.logger import logger

T_model = TypeVar('T_model', bound=msgspec.Struct)


class KeyValue(msgspec.Struct):
    key: str
    value: bytes
    # PRIMARY_KEY, AUTO_INCREMENT
    id: int = 0


class JwtSecret(msgspec.Struct):
    secret: bytes = b''
    macsha1: bytes = b''

    @staticmethod
    def host_macsha1():
        """
        Get the sha1(MAC_address) for current host.

        Returns:
            bytes: 20bit sha1, or empty bytes if failed to get  MAC on current host
        """
        import uuid
        from hashlib import sha1
        mac = uuid.getnode()
        if mac & (1 << 40):
            # uuid is just random bit, see uuid._random_getnode()
            return b''

        mac = mac.to_bytes(6, 'big')
        return sha1(mac).digest()


class AlasioKeyTable(AlasioConfigDB):
    TABLE_NAME = 'alasio'
    CREATE_TABLE = """
        CREATE TABLE "{TABLE_NAME}" (
        "id" INTEGER NOT NULL,
        "key" TEXT NOT NULL,
        "value" BLOB NOT NULL,
        PRIMARY KEY ("id"),
        UNIQUE ("key")
    );
    """
    MODEL = KeyValue

    def get(self, key, model_cls: Type[T_model]) -> "T_model | None":
        """
        Get value from key
        """
        row = self.select_one(key=key)
        if row is None:
            return model_cls()
        else:
            # found key
            value = row.value
            value = msgspec.msgpack.decode(value, type=model_cls)
            return value

    def get_value(self, key, default=None) -> "bytes | None":
        """
        Get value from key
        """
        row = self.select_one(key=key)
        if row is None:
            return default
        else:
            # found key
            value = row.value
            return value

    def set(self, key, model: T_model):
        """
        Set key value
        """
        value = msgspec.msgpack.encode(model)
        row = KeyValue(key=key, value=value)
        self.upsert_row(row, conflicts='key')

    def set_value(self, key, value: "bytes"):
        """
        Set key value
        """
        row = KeyValue(key=key, value=value)
        self.upsert_row(row, conflicts='key')

    @cached_property
    def jwt_secret(self):
        """
        Get global JWT secret, secret is different in different Alasio deploy.

        We use MAC address to track if config files are simply copied to another computer.
        Yes, MAC can be easily modified, but we don't need that level of security.

        Returns:
            bytes:
        """
        key = 'JwtSecret'
        length = 32
        value = self.get(key, JwtSecret)

        # check invalid secret
        if not value.secret:
            logger.info('New JWT secret (new secret)')
            import secrets
            value.secret = secrets.token_bytes(length)
            value.macsha1 = JwtSecret.host_macsha1()
            self.set(key, value)
            return value.secret
        if len(value.secret) != length:
            logger.info('New JWT secret (length not match)')
            import secrets
            value.secret = secrets.token_bytes(length)
            value.macsha1 = JwtSecret.host_macsha1()
            self.set(key, value)
            return value.secret

        # check if host changed
        macsha1 = JwtSecret.host_macsha1()
        if value.macsha1 != macsha1:
            logger.info('New JWT secret (MAC changed)')
            import secrets
            value.secret = secrets.token_bytes(length)
            value.macsha1 = macsha1
            self.set(key, value)
            return value.secret

        # keep using current
        return value.secret

    def mod_get(self):
        """
        Returns:
            str: Mod name or "" if unknown
        """
        try:
            value = self.get_value('Mod', '')
        except sqlite3.DatabaseError:
            # Broken file, or not a sqlite database
            return ''
        if not value:
            return ''
        try:
            return value.decode()
        except UnicodeDecodeError:
            return ''

    def mod_set(self, value):
        """
        Args:
            value (str):
        """
        value = value.encode()
        self.set_value('Mod', value)
        cached_property.pop(self, 'mod')
