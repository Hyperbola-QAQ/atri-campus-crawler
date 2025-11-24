import redis.asyncio as redis
from typing import Optional

_REDIS_CLIENT: Optional[redis.Redis] = None

# TODO 通过注册发放服务下发redis配置
def get_redis_client() -> Optional[redis.Redis]:
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis.Redis(
            host='localhost', 
            port=6379, 
            db=0, 
            decode_responses=True
        )
    return _REDIS_CLIENT