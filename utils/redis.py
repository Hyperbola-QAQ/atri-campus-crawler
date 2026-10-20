"""直接从进程环境配置的 Redis 客户端。"""

import os

import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()
load_dotenv(f".env.{os.getenv('ENVIRONMENT', 'dev')}")

_REDIS_CLIENT: redis.Redis | None = None


async def close_redis_client() -> None:
    """先清引用再关闭连接池以避免重复释放；生命周期须等所有使用者结束后调用。"""
    global _REDIS_CLIENT
    client, _REDIS_CLIENT = _REDIS_CLIENT, None
    if client is not None:
        await client.aclose()


def reset_redis_client_for_tests() -> None:
    """仅供已自行关闭连接的测试清引用，不释放资源；生产须用 close_redis_client。"""
    global _REDIS_CLIENT
    _REDIS_CLIENT = None


async def get_redis_client() -> redis.Redis:
    """按 REDIS_* 环境复用进程客户端，空密码不认证；构造不探测连通性，命令失败由调用方处理。"""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=os.getenv("REDIS_DECODE_RESPONSES", "true").lower()
            in {"1", "true", "yes"},
            ssl=os.getenv("REDIS_SSL", "false").lower() in {"1", "true", "yes"},
            socket_connect_timeout=float(
                os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "1")
            ),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT", "1")),
        )
    return _REDIS_CLIENT
