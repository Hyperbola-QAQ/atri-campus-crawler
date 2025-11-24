import pytest
from pathlib import Path
from utils.redis import get_redis_client
from utils.log import logger

# 脚本当前的绝对路径
SCRIPT_DIR = Path(__file__).parent


@pytest.mark.asyncio
async def test_get_redis_client():
    """测试获取Redis客户端"""
    redis_client = await get_redis_client()
    assert redis_client is not None
    await redis_client.ping()
    # 尝试写入
    await redis_client.set("test_key", "test_value")
    # 尝试读取
    value = await redis_client.get("test_key")
    logger.info(f"Redis测试键值: {value}")
    assert value == "test_value"
    # 清理测试键
    await redis_client.delete("test_key")
