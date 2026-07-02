from huey import RedisHuey
from app.constant import REDIS_HOST, REDIS_PORT, REDIS_DB


huey = RedisHuey(
    name='huey-schedule',
    utc=False,
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB
)


