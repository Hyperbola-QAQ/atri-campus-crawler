"""Daily electricity-reading cache with Redis and in-process fallback."""

import asyncio
import copy
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Awaitable, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from redis.exceptions import RedisError

from utils.redis import get_redis_client

logger = logging.getLogger(__name__)


class AsyncKeyValueStore(Protocol):
    def get(self, key: str, /) -> Awaitable[str | bytes | None]: ...

    def set(self, key: str, value: str, /, *, ex: int) -> Awaitable[Any]: ...


class DailyElectricityCache:
    """Cache readings through the next local midnight, with a memory fallback."""

    def __init__(
        self,
        *,
        school: str = "HNUCM",
        redis_client: AsyncKeyValueStore | None = None,
        key_prefix: str | None = None,
        timezone_name: str | None = None,
    ):
        self.school = school
        self._redis_client = redis_client
        self._key_prefix = key_prefix or os.getenv(
            "ELECTRICITY_CACHE_KEY_PREFIX", "electricity:daily"
        )
        configured_timezone = timezone_name or os.getenv(
            "ELECTRICITY_CACHE_TIMEZONE", "Asia/Shanghai"
        )
        try:
            self._timezone = ZoneInfo(configured_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                "ELECTRICITY_CACHE_TIMEZONE 必须是有效的 IANA 时区名称"
            ) from exc
        self._memory_entries: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._memory_lock = asyncio.Lock()
        self._redis_retry_at = 0.0

    @classmethod
    def from_environment(cls, school: str = "HNUCM") -> "DailyElectricityCache":
        return cls(school=school)

    def _day_and_expiry(self, now: datetime | None = None) -> tuple[str, datetime, int]:
        current = now or datetime.now(self._timezone)
        next_midnight = (current + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        ttl_seconds = max(1, int((next_midnight - current).total_seconds()))
        return current.date().isoformat(), next_midnight, ttl_seconds

    def _key(self, campus: str, room_number: str, day: str) -> str:
        return f"{self._key_prefix}:{self.school}:{day}:{campus}:{room_number}"

    async def _client(self) -> AsyncKeyValueStore:
        if self._redis_client is None:
            self._redis_client = await get_redis_client()
        return self._redis_client

    def _may_use_redis(self) -> bool:
        return asyncio.get_running_loop().time() >= self._redis_retry_at

    def _mark_redis_unavailable(self) -> None:
        retry_seconds = float(os.getenv("REDIS_RETRY_INTERVAL_SECONDS", "30"))
        self._redis_retry_at = asyncio.get_running_loop().time() + max(0, retry_seconds)

    async def _memory_get(self, key: str) -> dict[str, Any] | None:
        now = datetime.now(self._timezone)
        async with self._memory_lock:
            entry = self._memory_entries.get(key)
            if entry is None:
                return None
            expires_at, result = entry
            if expires_at <= now:
                self._memory_entries.pop(key, None)
                return None
            return copy.deepcopy(result)

    async def _memory_set(
        self, key: str, result: dict[str, Any], expires_at: datetime
    ) -> None:
        async with self._memory_lock:
            self._memory_entries[key] = (expires_at, copy.deepcopy(result))

    async def get(self, campus: str, room_number: str) -> dict[str, Any] | None:
        day, _expires_at, _ttl = self._day_and_expiry()
        key = self._key(campus, room_number, day)
        if self._may_use_redis():
            try:
                raw_value = await (await self._client()).get(key)
                if raw_value is not None:
                    result = json.loads(raw_value)
                    if isinstance(result, dict):
                        return result
                    logger.warning(
                        "Ignoring invalid electricity cache value from Redis"
                    )
            except (RedisError, OSError, ValueError, TypeError):
                self._mark_redis_unavailable()
                logger.warning(
                    "Redis unavailable; using process-local electricity cache"
                )
        return await self._memory_get(key)

    async def set(self, campus: str, room_number: str, result: dict[str, Any]) -> None:
        day, expires_at, ttl_seconds = self._day_and_expiry()
        key = self._key(campus, room_number, day)
        if self._may_use_redis():
            try:
                await (await self._client()).set(
                    key, json.dumps(result, ensure_ascii=False), ex=ttl_seconds
                )
            except (RedisError, OSError, ValueError, TypeError):
                self._mark_redis_unavailable()
                logger.warning(
                    "Redis unavailable; storing electricity cache in this process"
                )
        await self._memory_set(key, result, expires_at)
