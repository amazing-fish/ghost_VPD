import json

from redis import Redis

from app.constant import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_MAX_CONNECTIONS

redis_cli = Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,
    max_connections=REDIS_MAX_CONNECTIONS
)


class Cache:
    """缓存数据"""
    def __init__(self, client):
        self.client = client

    def set_value(self, hash_key, title, value):
        """缓存数据"""
        self.client.hset(hash_key, title, json.dumps(value))
        self.client.expire(hash_key, 60 * 60 * 4)

    def get_value(self, hash_key, title):
        """获取数据"""
        value = self.client.hget(hash_key, title)
        if value:
            value = json.loads(value)
        return value

    def delete_hash_title(self, hash_key, *title):
        """删除hash字段"""
        return self.client.hdel(hash_key, *title)

    def delete_hash_value(self, hash_key):
        """删除hash数据"""
        return self.client.delete(hash_key)

    def get_hash_titles(self, hash_key):
        """获取hash下所有title"""
        return self.client.hkeys(hash_key)
    
    def acquire_hash_lock(self, hash_key, title):
        """获取执行锁"""
        field = f'{title}_lock'
        return self.client.hsetnx(hash_key, field, 'locking')
    
    def release_hash_lock(self, hash_key, title):
        """释放执行锁"""
        field = f'{title}_lock'
        return self.client.hdel(hash_key, field)
    

cache = Cache(redis_cli)
