from abc import ABCMeta, abstractmethod
from contextlib import contextmanager
from json import dumps, loads
from typing import AnyStr, ContextManager, Generic, TypeVar

from . import redis_cli


__all__ = [
    'StrStorage',
    'JSONStorage',
    'HashStorage',
    'StrCache',
    'JSONCache',
    'HashCache'
]

T = TypeVar('T')


class StorageABC(Generic[T], metaclass=ABCMeta):
    """Redis存储抽象基类"""

    def __init__(self, key: AnyStr):
        self.key = key

    @abstractmethod
    def get(self) -> T:
        """读数据"""
        ...

    @abstractmethod
    def set(self, value: T, **kwargs):
        """写数据"""
        ...

    def delete(self):
        """删除数据"""
        redis_cli.delete(self.key)


class StrStorage(StorageABC):
    """字符串存储"""

    def get(self) -> T:
        return redis_cli.get(self.key) or ''

    def set(self, value: T, **kwargs):
        if value:
            redis_cli.set(self.key, value, **kwargs)


class JSONStorage(StorageABC):
    """字典转为JSON字符串存储"""

    def get(self) -> T:
        data = redis_cli.get(self.key)
        return loads(data) if data else {}

    def set(self, value: T, **kwargs):
        if value:
            redis_cli.set(self.key, dumps(value), **kwargs)


class HashStorage(StorageABC):
    """字典转为哈希表存储"""

    def get(self) -> T:
        return redis_cli.hgetall(self.key)

    def set(self, value: T, **kwargs):
        if value:
            redis_cli.hmset(self.key, value)
            ex = kwargs.get('ex')
            if ex:
                redis_cli.expire(self.key, ex)

    def get_field(self, field: AnyStr) -> str:
        """读一个字段"""
        return redis_cli.hget(self.key, field) or ''

    def set_field(self, field: AnyStr, value: AnyStr):
        """写一个字段"""
        redis_cli.hset(self.key, field, value)

    def incr_field(self, field: AnyStr, amount: int = 1) -> int:
        """增/减一个字段的数值"""
        return redis_cli.hincrby(self.key, field, amount=amount)

    def delete_field(self, field: AnyStr):
        """删除一个字段"""
        redis_cli.hdel(self.key, field)


class CacheABC(StorageABC[T], metaclass=ABCMeta):
    """Redis缓存抽象基类"""

    def set(self, value: T, expires: int = 3600, **kwargs):
        super().set(value, ex=expires, **kwargs)

    @contextmanager
    def delete_after(self) -> ContextManager:
        """操作结束后删除缓存"""
        try:
            yield
        finally:
            self.delete()


class StrCache(CacheABC, StrStorage):
    """字符串缓存"""
    pass


class JSONCache(CacheABC, JSONStorage):
    """字典转为JSON字符串缓存"""
    pass


class HashCache(CacheABC, HashStorage):
    """字典转为哈希表缓存"""
    pass
