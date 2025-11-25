import pytest
from pathlib import Path
from utils.redis import get_redis_client, reset_redis_client_for_tests
from utils.log import logger
import redis.asyncio as redis

SCRIPT_DIR = Path(__file__).parent

@pytest.mark.asyncio
async def test_get_redis_client():
    """测试获取Redis客户端"""
    reset_redis_client_for_tests()  # sync call
    
    try:
        redis_client: redis.Redis = await get_redis_client()
        await redis_client.ping()
        
        await redis_client.set("test_key", "test_value")
        value = await redis_client.get("test_key")
        logger.info(f"Redis测试键值: {value}")
        assert value == "test_value"
        
    finally:
        # 清理
        client = await get_redis_client()
        await client.delete("test_key")
        # 关闭连接（测试结束）
        await client.aclose()
        reset_redis_client_for_tests()