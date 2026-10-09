"""Redis client configured directly from the process environment."""

import os

import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()
load_dotenv(f".env.{os.getenv('ENVIRONMENT', 'dev')}")

_REDIS_CLIENT: redis.Redis | None = None


async def close_redis_client() -> None:
    """Release the owned connection pool on application shutdown."""
    global _REDIS_CLIENT
    client, _REDIS_CLIENT = _REDIS_CLIENT, None
    if client is not None:
        await client.aclose()


def reset_redis_client_for_tests() -> None:
    """Reset the shared client after a test closes it."""
    global _REDIS_CLIENT
    _REDIS_CLIENT = None


async def get_redis_client() -> redis.Redis:
    """Return a shared Redis client using REDIS_* environment settings."""
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
