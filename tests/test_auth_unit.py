"""教务认证的离线协议、重试和缓存回归测试。"""

from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from adapter.hnucm_adapter import auth


async def test_wrong_password_cannot_reuse_another_credentials_cookie(monkeypatch):
    stored = {}

    async def read(school, username, domain):
        return stored.get((school, username), httpx.Cookies())

    async def save(school_name, username, cookies):
        stored[(school_name, username)] = cookies

    async def login(**kwargs):
        if kwargs["password"] != "correct":
            raise ValueError("登录失败")
        return httpx.Cookies({"sid": "verified"})

    monkeypatch.setattr(auth, "is_in_maintenance_window", lambda: False)
    monkeypatch.setattr(auth, "get_cookies_from_redis", read)
    monkeypatch.setattr(auth, "save_cookies_to_redis", save)
    fresh_login = AsyncMock(side_effect=login)
    monkeypatch.setattr(auth, "get_cookies_from_jwxt", fresh_login)
    install_transport(monkeypatch, lambda request: httpx.Response(200, text="培养管理"))
    args = ("https://portal.test", "school", 1, {}, "student")
    assert (await auth.get_valid_cookies(*args, "correct")).get("sid") == "verified"
    with pytest.raises(ValueError, match="登录失败"):
        await auth.get_valid_cookies(*args, "wrong")
    assert (await auth.get_valid_cookies(*args, "correct")).get("sid") == "verified"
    assert fresh_login.await_count == 2


@pytest.mark.parametrize(
    "hour,minute,expected",
    [(0, 54, False), (0, 55, True), (7, 5, True), (7, 6, False), (23, 59, False)],
)
def test_maintenance_boundaries(monkeypatch, hour, minute, expected):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert str(tz) == "Asia/Shanghai"
            return cls(2026, 10, 7, hour, minute, tzinfo=tz)

    monkeypatch.setattr(auth, "datetime", Clock)
    assert auth.is_in_maintenance_window() is expected


@pytest.mark.parametrize("mode", ["none", "unavailable", "success", "failure"])
async def test_save_cookie_cache(monkeypatch, mode):
    redis = AsyncMock()
    redis.setex.side_effect = (
        RuntimeError("write failed") if mode == "failure" else None
    )
    monkeypatch.setattr(
        auth,
        "get_redis_client",
        AsyncMock(
            return_value=None if mode == "none" else redis,
            side_effect=RuntimeError("offline") if mode == "unavailable" else None,
        ),
    )
    cookies = httpx.Cookies()
    cookies.set("sid", "one", domain="a.test")
    cookies.set("sid", "two", domain="b.test")
    if mode == "failure":
        with pytest.raises(ValueError, match="存储Cookie到Redis失败"):
            await auth.save_cookies_to_redis("school", "student", cookies)
    else:
        await auth.save_cookies_to_redis("school", "student", cookies)
    if mode in {"success", "failure"}:
        redis.setex.assert_awaited_once_with(
            "cookies:school:student", 1800, "sid=one; sid=two"
        )


@pytest.mark.parametrize(
    "value", [None, "sid=a=b; invalid; token=ok", b"sid=a=b; invalid; token=ok"]
)
async def test_read_cookie_cache(monkeypatch, value):
    redis = AsyncMock()
    redis.get.return_value = value
    monkeypatch.setattr(auth, "get_redis_client", AsyncMock(return_value=redis))
    cookies = await auth.get_cookies_from_redis("school", "student", "portal.test")
    redis.get.assert_awaited_once_with("cookies:school:student")
    assert dict(cookies) == ({} if value is None else {"sid": "a=b", "token": "ok"})
    assert all(cookie.domain == "portal.test" for cookie in cookies.jar)


@pytest.mark.parametrize("mode", ["none", "unavailable", "failure"])
async def test_read_cookie_cache_failure(monkeypatch, mode):
    redis = AsyncMock()
    redis.get.side_effect = RuntimeError("offline")
    monkeypatch.setattr(
        auth,
        "get_redis_client",
        AsyncMock(
            return_value=None if mode == "none" else redis,
            side_effect=RuntimeError("offline") if mode == "unavailable" else None,
        ),
    )
    if mode == "failure":
        with pytest.raises(ValueError, match="从Redis获取Cookie失败"):
            await auth.get_cookies_from_redis("school", "student")
    else:
        assert not await auth.get_cookies_from_redis("school", "student")


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        auth.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "redirect",
        "rejected",
        "network",
        "unknown",
        "bad_session",
        "ocr_failed",
        "ocr_retry",
    ],
)
async def test_login_protocol(monkeypatch, mode):
    seen = []

    def handler(request):
        seen.append(request)
        if mode == "network":
            raise httpx.ConnectError("offline", request=request)
        if mode == "unknown":
            raise RuntimeError("broken transport")
        if request.url.path == "/verifycode.servlet":
            return httpx.Response(200, content=b"captcha")
        if request.url.params.get("flag") == "sess":
            return httpx.Response(
                200,
                text="bad"
                if mode == "bad_session"
                else "abcdefghijklmnopqrst#" + "1" * 20,
            )
        if request.method == "POST":
            from urllib.parse import parse_qs

            body = parse_qs(request.content.decode(), keep_blank_values=True)
            assert body["RANDOMCODE"] == ["1234"]
            assert body["userPassword"] == [""]
            original = "student%%%a-very-long-password"
            expected = (
                "".join(a + b for a, b in zip(original[:20], "abcdefghijklmnopqrst"))
                + original[20:]
            )
            assert body["encoded"] == [expected]
            return httpx.Response(
                302 if mode == "redirect" else 200,
                text="denied" if mode == "rejected" else "培养管理",
                headers={"set-cookie": "sid=valid; Path=/"},
            )
        return httpx.Response(200, text="培养管理")

    install_transport(monkeypatch, handler)
    ocr = AsyncMock(return_value="1234")
    if mode == "ocr_failed":
        ocr.side_effect = ValueError("unreadable")
    elif mode == "ocr_retry":
        ocr.side_effect = [ValueError("unreadable"), "1234"]
    monkeypatch.setattr(auth, "img2txt", ocr)
    if mode in {"success", "redirect", "ocr_retry"}:
        cookies = await auth.get_cookies_from_jwxt(
            "https://portal.test", 1, {}, "student", "a-very-long-password"
        )
        assert cookies["sid"] == "valid"
        assert ocr.await_count == (2 if mode == "ocr_retry" else 1)
    else:
        with pytest.raises(ValueError):
            await auth.get_cookies_from_jwxt(
                "https://portal.test", 1, {}, "student", "a-very-long-password"
            )
        if mode == "ocr_failed":
            assert ocr.await_count == 5


@pytest.mark.parametrize(
    "mode",
    [
        "cached",
        "renew_failed",
        "expired",
        "empty",
        "read_failed",
        "login_value",
        "login_unknown",
        "save_value",
        "save_unknown",
        "maintenance",
    ],
)
async def test_valid_cookie_selection(monkeypatch, mode):
    cached = httpx.Cookies({"sid": "cached"})
    fresh = httpx.Cookies({"sid": "fresh"})
    monkeypatch.setattr(auth, "is_in_maintenance_window", lambda: mode == "maintenance")
    read = AsyncMock(
        return_value=httpx.Cookies() if mode == "empty" else cached,
        side_effect=ValueError("read") if mode == "read_failed" else None,
    )
    login = AsyncMock(return_value=fresh)
    save = AsyncMock()
    if mode == "login_value":
        login.side_effect = ValueError("rejected")
    if mode == "login_unknown":
        login.side_effect = RuntimeError("unexpected")
    if mode in {"renew_failed", "save_unknown"}:
        save.side_effect = RuntimeError("offline")
    if mode == "save_value":
        save.side_effect = ValueError("offline")
    monkeypatch.setattr(auth, "get_cookies_from_redis", read)
    monkeypatch.setattr(auth, "get_cookies_from_jwxt", login)
    monkeypatch.setattr(auth, "save_cookies_to_redis", save)
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, text="培养管理" if mode in {"cached", "renew_failed"} else "expired"
        ),
    )
    if mode in {"maintenance", "login_value", "login_unknown"}:
        with pytest.raises(ValueError):
            await auth.get_valid_cookies(
                "https://portal.test", "school", 1, {}, "student", "password"
            )
    else:
        result = await auth.get_valid_cookies(
            "https://portal.test", "school", 1, {}, "student", "password"
        )
        assert result is (cached if mode in {"cached", "renew_failed"} else fresh)
        read.assert_awaited_once_with(
            "school",
            auth.credential_cache_identity(
                "https://portal.test", "student", "password"
            ),
            "portal.test",
        )
        if mode in {"cached", "renew_failed"}:
            login.assert_not_awaited()
        else:
            login.assert_awaited_once()
