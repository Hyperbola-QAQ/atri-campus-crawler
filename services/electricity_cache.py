"""按学校、日期、校区及规范寝室身份缓存日读数，本地午夜失效。
Redis 故障退避并降级进程内副本，不能作为持久化事实来源。
"""

import asyncio
import copy
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Awaitable, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from redis.exceptions import RedisError

from utils.electricity_identity import normalize_room_number
from utils.redis import get_redis_client

logger = logging.getLogger(__name__)


def _matches_room(result: dict[str, Any], room_number: str) -> bool:
    """旧载荷缺少寝室字段时兼容，显式身份必须有效且与请求一致。"""
    value = result.get("room_number")
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        return normalize_room_number(value) == normalize_room_number(room_number)
    except ValueError:
        return False


def _matches_identity(result: dict[str, Any], campus: str, room_number: str) -> bool:
    """校区字段缺失时兼容旧载荷，显式校区和寝室须匹配；学校由键隔离。"""
    return ("campus" not in result or result["campus"] == campus) and _matches_room(
        result, room_number
    )


class AsyncKeyValueStore(Protocol):
    """日缓存使用的最小异步接口；读取兼容文本或字节，ex 使用秒。"""

    def get(self, key: str, /) -> Awaitable[str | bytes | None]: ...

    def set(self, key: str, value: str, /, *, ex: int) -> Awaitable[Any]: ...


class DailyElectricityCache:
    """Redis 优先，未命中、无效值或故障时读取本实例内存副本，重启不保留。"""

    def __init__(
        self,
        *,
        school: str = "HNUCM",
        redis_client: AsyncKeyValueStore | None = None,
        key_prefix: str | None = None,
        timezone_name: str | None = None,
        max_memory_entries: int = 50000,
    ):
        """显式参数优先于环境；非法时区或非正容量拒绝构造，不在此时连接 Redis。"""
        self.school = school
        if max_memory_entries < 1:
            raise ValueError("max_memory_entries must be positive")
        self._max_memory_entries = max_memory_entries
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
        self._last_memory_cleanup_day: str | None = None
        self._redis_retry_at = 0.0

    @classmethod
    def from_environment(cls, school: str = "HNUCM") -> "DailyElectricityCache":
        """按环境约定构造缓存，不探测 Redis 或加载目录。"""
        return cls(school=school)

    def _day_and_expiry(self, now: datetime | None = None) -> tuple[str, datetime, int]:
        """一次采样得到本地日期、下次午夜和至少一秒 TTL，避免跨日键与期限错位。"""
        current = now or datetime.now(self._timezone)
        next_midnight = (current + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        ttl_seconds = max(1, int((next_midnight - current).total_seconds()))
        return current.date().isoformat(), next_midnight, ttl_seconds

    def _key(self, campus: str, room_number: str, day: str) -> str:
        """规范寝室身份后生成版本化日键，保留学校、校区和楼类隔离。"""
        return f"{self._key_prefix}:v2:{self.school}:{day}:{campus}:{normalize_room_number(room_number)}"

    async def _client(self) -> AsyncKeyValueStore:
        """复用注入或延迟获取的 Redis 客户端；连接池由应用管理，本缓存不关闭。"""
        if self._redis_client is None:
            self._redis_client = await get_redis_client()
        return self._redis_client

    def _may_use_redis(self) -> bool:
        """用单调时钟判断退避是否结束，避免系统校时影响重试。"""
        return asyncio.get_running_loop().time() >= self._redis_retry_at

    def _mark_redis_unavailable(self) -> None:
        """按配置记录退避期限，负间隔按零处理，不清空内存副本。"""
        retry_seconds = float(os.getenv("REDIS_RETRY_INTERVAL_SECONDS", "30"))
        self._redis_retry_at = asyncio.get_running_loop().time() + max(0, retry_seconds)

    async def _memory_get(self, key: str) -> dict[str, Any] | None:
        """返回未过午夜的深拷贝，避免调用方修改共享读数。"""
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
        """深拷贝写入并限制容量，完整过期扫描每天最多一次，超量按插入顺序淘汰。"""
        async with self._memory_lock:
            now = datetime.now(self._timezone)
            day = now.date().isoformat()
            if self._last_memory_cleanup_day != day:
                for old_key, (expiry, _) in list(self._memory_entries.items()):
                    if expiry <= now:
                        del self._memory_entries[old_key]
                self._last_memory_cleanup_day = day
            self._memory_entries[key] = (expires_at, copy.deepcopy(result))
            while len(self._memory_entries) > self._max_memory_entries:
                self._memory_entries.pop(next(iter(self._memory_entries)))

    async def get(self, campus: str, room_number: str) -> dict[str, Any] | None:
        """仅接受匹配身份的 JSON 对象；Redis 异常安全降级，内存也未命中则返回 None。"""
        day, _expires_at, _ttl = self._day_and_expiry()
        key = self._key(campus, room_number, day)
        if self._may_use_redis():
            try:
                raw_value = await (await self._client()).get(key)
                if raw_value is not None:
                    result = json.loads(raw_value)
                    if isinstance(result, dict) and _matches_identity(
                        result, campus, room_number
                    ):
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
        """身份不匹配时拒绝写入；Redis 失败仍写内存，成功不代表共享层已保存。"""
        if not _matches_identity(result, campus, room_number):
            raise ValueError("电费缓存的寝室身份不一致")
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
