import redis.asyncio as redis
import json
import os
from typing import Optional
from .nacos import get_nacos_config_client
from v2.nacos import ConfigParam
from dotenv import load_dotenv
from utils.log import logger

load_dotenv(".env")
load_dotenv(f'.env.{os.getenv("ENVIRONMENT")}')

_REDIS_CLIENT: Optional[redis.Redis] = None

def reset_redis_client_for_tests() -> None:
    """仅供测试使用：重置全局 Redis 客户端"""
    global _REDIS_CLIENT
    _REDIS_CLIENT = None

async def get_redis_client() -> redis.Redis:
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        config_client = await get_nacos_config_client()
        if config_client is None:
            logger.warning("获取nacos配置客户端失败, 使用默认配置")
            _REDIS_CLIENT = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
            return _REDIS_CLIENT

        config_param = ConfigParam(data_id="dev.redis.json", group="REDIS")
        try:
            config_str = await config_client.get_config(config_param)
            if config_str:
                config: dict = json.loads(config_str)
                _REDIS_CLIENT = redis.Redis(
                    host=config.get("host", "localhost"),
                    port=config.get("port", 6379),
                    db=config.get("db", 0),
                    decode_responses=config.get("decode_responses", True),
                    password=config.get("password"),
                    ssl=config.get("ssl", False),
                )
                await _REDIS_CLIENT.ping()
                return _REDIS_CLIENT
        except Exception as e:
            logger.warning(f"获取Redis配置失败: {e}, 使用默认配置")

        # fallback
        _REDIS_CLIENT = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
    
    return _REDIS_CLIENT