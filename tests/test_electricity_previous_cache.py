"""昨日缓存只用于比较，不会当作今日读数返回，也不访问财务平台补历史。"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from services import electricity_cache as module


async def test_retains_yesterday_without_serving_it_as_today(monkeypatch):
    state = {"now": datetime(2026, 10, 23, 7, tzinfo=ZoneInfo("Asia/Shanghai"))}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"].astimezone(tz)

    class Redis:
        values = {}
        ttl = None

        async def set(self, key, value, *, ex):
            self.ttl = ex
            self.values[key] = (value, state["now"] + timedelta(seconds=ex))

        async def get(self, key):
            value = self.values.get(key)
            return value[0] if value and value[1] > state["now"] else None

    monkeypatch.setattr(module, "datetime", Clock)
    redis = Redis()
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=redis))
    cache = module.DailyElectricityCache.from_environment()
    yesterday = state["now"].date()
    await cache.set("area", "06627", {"balance": 22.44})
    assert redis.ttl == 41 * 3600
    state["now"] += timedelta(days=1)
    assert await cache.get("area", "06627") is None
    assert await cache.get("area", "06627", day=yesterday) == {"balance": 22.44}
    await cache.set("area", "06627", {"balance": 16.47})
    assert await cache.get("area", "06627") == {"balance": 16.47}
    assert await cache.get("area", "06627", day=yesterday) == {"balance": 22.44}
    state["now"] += timedelta(days=1)
    assert await cache.get("area", "06627", day=yesterday) is None
