import ast
import logging
from settings.redis_db import redis_db
from abc import ABC
import msgpack

from systems.structures.redis_storage import RedisStorageException, MuchException

logger = logging.getLogger(__name__)
ENCODING = 'utf-8'


class KeyValueException(RedisStorageException):
    pass


class KeyValueStorage(ABC):
    """
    stores things in redis database given a key and value
    by default uses the instance class name as the key
    recommend to uniquely identify the instance with a key prefix or suffix
    prefixes are for general parent classes objects (eg. mammal:human:woman:Lisa )
    suffixes are for specific attributes (eg. Lisa:eye_color, Lisa:age, etc)
    """
    _db_key: str = ""  # default will be self.__class__.__name__
    _db_value: bytes = None
    _value: bytes = None

    # _value_type = None

    def __init__(self, *args, **kwargs):
        # key for redis storage
        self._key_prefix = kwargs.get('key_prefix', "")
        self._key = kwargs.get('key', self.__class__.__name__)
        self._key_suffix = kwargs.get('key_suffix', "")
        self.force_save = kwargs.get('force_save', False)
        if 'value' in kwargs:
            self.value = kwargs.get('value')

    def __str__(self):
        return str(self.get_db_key())

    def get_db_key(self, refresh=False):
        if refresh or not self._db_key:
            self._db_key = self.compile_db_key(
                key_prefix=self._key_prefix,
                key=self._key,
                key_suffix=self._key_suffix
            )
            # todo: add this line for using env in key
            # + "" if SIMULATED_ENV == "PRODUCTION" else str(SIMULATED_ENV)
        return self._db_key

    @property
    def value(self):
        if self._value is None:
            if self._db_value is None:
                self._db_value = self._get_db_value(self.get_db_key())
                self._value = self._db_value  # may stay None
        if self._value is not None:
            return msgpack.loads(self._value)
        return None

    @value.setter
    def value(self, new_value):
        # self._value_type = type(new_value)
        self._value = msgpack.dumps(new_value)

    def save(self, pipeline=None, *args, **kwargs):
        if self._value is None:
            raise KeyValueException("no value set, use delete() to permanently remove")
        if not self.force_save:
            # validate some rules here?
            pass
        # logger.debug(f'savingkey, value: {self.get_db_key()}, {self.value}')

        if pipeline is not None:
            pipeline = pipeline.set(self.get_db_key(), self._value)
            self._db_value = None  # becomes unknown
            return pipeline
        else:
            db_response = redis_db.set(self.get_db_key(), self._value)
            if db_response is True:
                self._db_value = self._value
            return db_response

    def delete(self, pipeline=None, *args, **kwargs):
        if pipeline is not None:
            pipeline = pipeline.delete(self.get_db_key())
            return pipeline
        else:
            db_response = redis_db.delete(self.get_db_key())
            if db_response >= 0:
                return True

    def revert(self):
        self._db_value = self._get_db_value(self.get_db_key(refresh=True))
        self._value = self._db_value

    @classmethod
    def compile_db_key(cls, key: str, key_prefix: str, key_suffix: str) -> str:
        key = key or cls.__name__
        return str(
            f'{key_prefix.strip(":")}:' +
            f'{key.strip(":")}' +
            f':{key_suffix.strip(":")}'
        ).replace("::", ":").strip(":")

    @classmethod
    def _get_db_value(cls, db_key: str = "", *args, **kwargs):
        return redis_db.get(db_key) if db_key else None  # also returns None if key not found

