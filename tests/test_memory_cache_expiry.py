"""不访问 Redis，以过期冷条目验证写入清理，避免仅 get 清理掩盖跨日滞留。"""

from datetime import datetime, timedelta

from services.electricity_cache import DailyElectricityCache


async def test_new_day_write_evicts_unread_expired_entries():
    cache = DailyElectricityCache()
    now = datetime.now(cache._timezone)
    cache._memory_entries["yesterday"] = (now - timedelta(seconds=1), {"balance": 1})
    cache._memory_entries["today"] = (now + timedelta(hours=1), {"balance": 2})
    await cache._memory_set("new", {"balance": 3}, now + timedelta(hours=1))
    assert set(cache._memory_entries) == {"today", "new"}
