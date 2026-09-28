import redis.asyncio as redis
import pytest

from utils import redis as redis_module
from utils.log import logger
from utils.redis import get_redis_client, reset_redis_client_for_tests


@pytest.mark.asyncio
async def test_get_redis_client_uses_environment(monkeypatch):
    settings = {}
    client = object()

    def fake_client(**kwargs):
        settings.update(kwargs)
        return client

    monkeypatch.setenv("REDIS_HOST", "redis.example")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_DB", "2")
    monkeypatch.setenv("REDIS_PASSWORD", "test-secret")
    monkeypatch.setenv("REDIS_DECODE_RESPONSES", "false")
    monkeypatch.setenv("REDIS_SSL", "true")
    monkeypatch.setattr(redis_module.redis, "Redis", fake_client)
    reset_redis_client_for_tests()

    try:
        assert await get_redis_client() is client
        assert await get_redis_client() is client
        assert settings == {
            "host": "redis.example",
            "port": 6380,
            "db": 2,
            "password": "test-secret",
            "decode_responses": False,
            "ssl": True,
        }
    finally:
        reset_redis_client_for_tests()


@pytest.mark.asyncio
@pytest.mark.infrastructure
async def test_get_redis_client():
    """测试获取Redis客户端"""
    reset_redis_client_for_tests()  # sync call
    redis_client: redis.Redis | None = None
    try:
        redis_client = await get_redis_client()
        await redis_client.ping()

        await redis_client.set("test_key", "test_value")
        value = await redis_client.get("test_key")
        logger.info(f"Redis测试键值: {value}")
        assert value == "test_value"
    finally:
        if redis_client is not None:
            await redis_client.delete("test_key")
            await redis_client.aclose()
        reset_redis_client_for_tests()
