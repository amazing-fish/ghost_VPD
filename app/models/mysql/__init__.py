from contextlib import contextmanager
from os import getenv

from playhouse.pool import PooledMySQLDatabase

from app.constant import MYSQL_USER, MYSQL_HOST, MYSQL_DB, MYSQL_PASSWORD, REMOTE_MODE


class GatewayMockDatabase(PooledMySQLDatabase):
    def _connect(self):
        return True

    def connect(self, reuse_if_open=False):
        if REMOTE_MODE:
            return True
        return super().connect(reuse_if_open)

    def close(self):
        if REMOTE_MODE:
            return True
        return super().close()

    def is_closed(self):
        if REMOTE_MODE:
            return False
        return super().is_closed()


if REMOTE_MODE:
    @contextmanager
    def nop_context(*args, **kwargs):
        yield

    db = GatewayMockDatabase('mock_db')
    db.atomic = nop_context
    db.transaction = nop_context
    db.savepoint = nop_context
    db._atomic = nop_context
else:
    db = PooledMySQLDatabase(
        MYSQL_DB,
        max_connections=int(getenv('MYSQL_MAX_CONNECTIONS') or 200),
        stale_timeout=int(getenv('MYSQL_STALE_TIMEOUT') or 300),
        timeout=int(getenv('MYSQL_WAIT_TIMEOUT') or 0) or None,
        autoconnect=False,
        host=MYSQL_HOST,
        port=3306,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset='utf8mb4'
    )
