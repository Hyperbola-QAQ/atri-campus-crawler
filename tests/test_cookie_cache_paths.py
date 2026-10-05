"""缓存到期边界、凭据隔离及并发读写回归。"""

import asyncio

import pytest

from services.cookie_cache import InMemoryCookieCache, PortalSession


@pytest.mark.parametrize("ttl", [0, -1, 10])
async def test_expiry_boundary(monkeypatch, ttl):
    clock = [100.0]
    monkeypatch.setattr("services.cookie_cache.time.monotonic", lambda: clock[0])
    cache = InMemoryCookieCache(ttl)
    await cache.set("user", PortalSession({"sid": "secret"}, "csrf"))
    if ttl > 0:
        assert await cache.get("user") is not None
        clock[0] += ttl
    assert await cache.get("user") is None
    assert "user" not in cache._entries
    await cache.delete("missing")


async def test_copy_and_concurrent_user_isolation():
    cache = InMemoryCookieCache()
    cookies = {"sid": "original"}
    await cache.set("original", PortalSession(cookies, "csrf"))
    cookies["sid"] = "modified"
    result = await cache.get("original")
    assert result.cookies == {"sid": "original"}
    result.cookies.clear()
    assert (await cache.get("original")).cookies == {"sid": "original"}

    async def user(index):
        key = str(index)
        await cache.set(key, PortalSession({"sid": key}, key))
        for _ in range(3):
            value = await cache.get(key)
            assert value.cookies == {"sid": key}
            assert value.csrf_token == key
        await cache.delete(key)
        assert await cache.get(key) is None

    await asyncio.gather(*(user(index) for index in range(100)))
    assert list(cache._entries) == ["original"]
