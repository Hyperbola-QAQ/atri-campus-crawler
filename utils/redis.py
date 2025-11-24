import redis.asyncio as redis
import json
from typing import Optional
from .nacos import get_nacos_config_client
from v2.nacos import ConfigParam
from dotenv import load_dotenv
import os


load_dotenv(".env")
# 再根据环境变量加载对应的.env文件
load_dotenv(f'.env.{os.getenv("ENVIRONMENT")}')


_REDIS_CLIENT: Optional[redis.Redis] = None


async def get_redis_client() -> Optional[redis.Redis]:
    """获取Redis客户端，从nacos的ATRI-crawler命名空间中获取dev.redis.json配置"""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        # 获取nacos配置客户端
        config_client = await get_nacos_config_client()

        # 从nacos获取redis配置
        config_param = ConfigParam(
            data_id="dev.redis.json",
            group="REDIS",
        )

        try:
            # 获取配置
            config_str = await config_client.get_config(config_param)
            if config_str:
                # 解析配置
                config = json.loads(config_str)

                # 创建Redis客户端
                _REDIS_CLIENT = redis.Redis(
                    host=config.get("host", "localhost"),
                    port=config.get("port", 6379),
                    db=config.get("db", 0),
                    decode_responses=config.get("decode_responses", True),
                    password=config.get("password"),
                    ssl=config.get("ssl", False),
                )

                # 测试连接
                await _REDIS_CLIENT.ping()
        except Exception as e:
            logger.warning(f"获取Redis配置失败: {e}, 使用默认配置")
            # 如果获取失败，使用默认配置
            _REDIS_CLIENT = redis.Redis(
                host="localhost", port=6379, db=0, decode_responses=True
            )
    return _REDIS_CLIENT
