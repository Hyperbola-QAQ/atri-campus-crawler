import asyncio
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from adapter.hnucm_adapter.electricity import (
    ElectricityPlatformError,
    HNUCMElectricityClient,
    _jsbn_hex_to_base64,
    encrypt_password,
)
from services.cookie_cache import InMemoryCookieCache, PortalSession
from services.electricity_cache import DailyElectricityCache
from services.electricity import (
    AccountPoolConfigurationError,
    ElectricityAccountPool,
    ElectricityQueryError,
    ElectricityService,
)


def _write_accounts(path: Path, *accounts: tuple[str, str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {"xh": username, "pwd": password} for username, password in accounts
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_jsbn_hex_to_base64_matches_portal_conversion():
    assert _jsbn_hex_to_base64("000") == "AA=="
    assert _jsbn_hex_to_base64("fff") == "//=="
    assert _jsbn_hex_to_base64("ff") == "/w=="
    assert _jsbn_hex_to_base64("f") == "8==="


def test_electricity_client_uses_hnucm_portal_by_default():
    assert HNUCMElectricityClient().base_url == "http://cw-zfpt.hnucm.edu.cn"


@pytest.mark.asyncio
async def test_room_option_business_error_includes_upstream_reason():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "IsSuccess": False,
                "RetCode": "500",
                "RetMsg": "服务器内部错误",
                "Content": None,
            },
        )

    client = HNUCMElectricityClient()
    async with httpx.AsyncClient(
        base_url="https://payment.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        with pytest.raises(
            ElectricityPlatformError,
            match="500: 服务器内部错误",
        ):
            await client._get_room_options(http_client, "csrf", "area", {})


def test_password_is_rsa_encrypted_before_base64_conversion():
    ciphertext = encrypt_password("secret", "F" * 256, "123456")

    assert ciphertext
    assert "secret" not in ciphertext
    assert len(ciphertext) > 100


@pytest.mark.asyncio
async def test_portal_login_room_query_and_cookie_session_reuse():
    observed: list[httpx.Request] = []
    client_options: list[dict] = []
    electricity_attempts = 0
    public_key = "F" * 256

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal electricity_attempts
        observed.append(request)
        if request.url.path == "/xysf/api/Token/Csrf":
            assert request.method == "POST"
            assert json.loads(request.content) == {}
            return httpx.Response(200, json="csrf-token")
        if request.url.path == "/xysf/login.aspx":
            return httpx.Response(
                200,
                text=(
                    f'<input type="hidden" id="pbk" value="{public_key}">'
                    '<input type="hidden" id="ts" value="123456">'
                ),
            )
        if request.url.path == "/xysf/api/User/App/Login":
            assert request.headers["x-csrftoken"] == "csrf-token"
            login_data = json.loads(request.content)
            assert login_data["xh"] == "account"
            assert login_data["pwd"] != "password"
            assert login_data["rsaStr"] == "123456"
            assert login_data["ltyp"] == "id"
            return httpx.Response(
                200,
                json={"code": "0000"},
                headers={"set-cookie": "session=authenticated; Path=/"},
            )
        if request.url.path == "/xysf/api/user/ElecRoomYun/GetOption":
            assert request.headers["x-csrftoken"] == "csrf-token"
            option_request = json.loads(request.content)
            key = option_request["key"]
            option = option_request["option"]
            choices = {
                "area": [{"label": "含浦学生宿舍", "value": "area-1"}],
                "build": [{"label": "6号公寓", "value": "building-6"}],
                "level": [{"label": "6栋4层", "value": "level-4"}],
                "room": [
                    {
                        "label": f"{option['roomid'][-3:] if option['roomid'] != '-1' else '417'}房",
                        "value": "room-417"
                        if option["levelid"] == "level-4"
                        else "room-418",
                    }
                ],
            }
            if key == "room":
                choices["room"] = [
                    {"label": "417房", "value": "room-417"},
                    {"label": "418房", "value": "room-418"},
                ]
            return httpx.Response(
                200, json={"IsSuccess": True, "Content": choices[key]}
            )
        if request.url.path == "/xysf/aAppPage/index.aspx/GetRechargeInfo":
            electricity_attempts += 1
            assert request.headers["x-csrftoken"] == "csrf-token"
            body = json.loads(request.content)
            assert set(body) == {"rybh", "category"}
            assert body["category"] == "ElecRoomYun"
            room_selection = json.loads(body["rybh"])
            assert room_selection["roomid"] in {"room-417", "room-418"}
            assert room_selection["IsFirst"] is False
            assert request.headers.get("cookie") == "session=authenticated"
            if electricity_attempts == 1:
                return httpx.Response(200, json={"RetCode": "T", "Content": None})
            return httpx.Response(
                200,
                json={
                    "d": {
                        "RetCode": "T",
                        "Content": {
                            "Succ": True,
                            "CzThirdInfo": {
                                "Czxm": "6号公寓417房",
                                "Czzjh": "meter-1",
                                "PackageName": "193.17kWh",
                                "Balance": 119.57,
                                "State": "在线",
                                "Category": "ElecRoomYun",
                            },
                        },
                    }
                },
            )
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    client = HNUCMElectricityClient(
        "https://payment.example",
        client_factory=lambda **kwargs: (
            client_options.append(kwargs)
            or httpx.AsyncClient(transport=transport, **kwargs)
        ),
    )

    result, session = await client.query("06417", "hanpu", "account", "password")
    cached_result, refreshed_session = await client.query(
        "06418", "hanpu", "account", "password", session=session
    )

    assert result == {
        "campus": "hanpu",
        "room_number": "06417",
        "name": "6号公寓417房",
        "meter_number": "meter-1",
        "remaining_electricity": "193.17kWh",
        "balance": 119.57,
        "state": "在线",
        "category": "ElecRoomYun",
    }
    assert cached_result["room_number"] == "06418"
    assert session.cookies == {"session": "authenticated"}
    assert refreshed_session.cookies == session.cookies
    assert [request.url.path for request in observed].count("/xysf/api/Token/Csrf") == 1
    assert [request.url.path for request in observed].count(
        "/xysf/api/User/App/Login"
    ) == 1
    assert electricity_attempts == 3
    assert all(options["trust_env"] is False for options in client_options)


@pytest.mark.asyncio
async def test_portal_discovers_rooms_for_every_area():
    public_key = "F" * 256

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/xysf/api/Token/Csrf":
            return httpx.Response(200, json="csrf-token")
        if request.url.path == "/xysf/login.aspx":
            return httpx.Response(
                200,
                text=(
                    f'<input type="hidden" id="pbk" value="{public_key}">'
                    '<input type="hidden" id="ts" value="123456">'
                ),
            )
        if request.url.path == "/xysf/api/User/App/Login":
            return httpx.Response(200, json={"code": "0000"})
        if request.url.path == "/xysf/api/user/ElecRoomYun/GetOption":
            body = json.loads(request.content)
            key, option = body["key"], body["option"]
            choices = {
                "area": [
                    {"label": "含浦学生宿舍", "value": "campus-hanpu"},
                    {"label": "东塘学生宿舍", "value": "campus-dongtang"},
                ],
                "build": [
                    {"label": "6号公寓", "value": f"building-{option['areaid']}"}
                ],
                "level": [{"label": "6栋4层", "value": f"level-{option['buildid']}"}],
                "room": [{"label": "417房", "value": f"room-{option['levelid']}"}],
            }
            return httpx.Response(
                200, json={"IsSuccess": True, "Content": choices[key]}
            )
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    client = HNUCMElectricityClient(
        "https://payment.example",
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    rooms, _session = await client.discover_rooms("account", "password")

    assert rooms == [
        {
            "campus": "campus-hanpu",
            "campus_name": "含浦学生宿舍",
            "building": "6号公寓",
            "level": "6栋4层",
            "room": "417房",
            "room_number": "06417",
        },
        {
            "campus": "campus-dongtang",
            "campus_name": "东塘学生宿舍",
            "building": "6号公寓",
            "level": "6栋4层",
            "room": "417房",
            "room_number": "06417",
        },
    ]


@pytest.mark.asyncio
async def test_account_pool_rotates_and_retries_without_returning_account_data(
    tmp_path,
):
    _write_accounts(
        tmp_path / "accounts.json", ("account-a", "secret-a"), ("account-b", "secret-b")
    )
    attempted: list[tuple[str, bool]] = []

    class FakeClient:
        def __init__(self, _base_url: str):
            pass

        async def query(
            self,
            room_number: str,
            campus: str,
            username: str,
            password: str,
            session: PortalSession | None = None,
        ):
            attempted.append((username, session is not None))
            if username == "account-a":
                raise RuntimeError("login failed")
            assert password == "secret-b"
            return (
                {
                    "campus": campus,
                    "room_number": room_number,
                    "remaining_electricity": "5kWh",
                },
                PortalSession({"sid": username}, "csrf"),
            )

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        client_factory=FakeClient,
    )

    first_result = await service.query("06417", "hanpu")
    second_result = await service.query("06418", "hanpu")

    assert first_result == {
        "campus": "hanpu",
        "room_number": "06417",
        "remaining_electricity": "5kWh",
    }
    assert second_result["room_number"] == "06418"
    assert attempted == [
        ("account-a", False),
        ("account-b", False),
        ("account-b", True),
    ]
    assert "account" not in json.dumps(first_result)


@pytest.mark.asyncio
async def test_expired_cached_session_is_replaced_by_fresh_login(tmp_path):
    _write_accounts(tmp_path / "accounts.json", ("account", "secret"))
    calls: list[bool] = []

    class ExpiredSessionClient:
        def __init__(self, _base_url: str):
            pass

        async def query(self, room_number, campus, username, password, session=None):
            calls.append(session is not None)
            if session is not None:
                raise RuntimeError("session expired")
            return (
                {
                    "campus": campus,
                    "room_number": room_number,
                    "remaining_electricity": "2kWh",
                },
                PortalSession({"sid": "fresh"}, "new-csrf"),
            )

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        client_factory=ExpiredSessionClient,
    )
    account = (await service.account_pool.rotated_accounts())[0]
    await service.cookie_cache.set(
        account.cache_key, PortalSession({"sid": "expired"}, "old-csrf")
    )

    result = await service.query("06417", "hanpu")

    assert result["remaining_electricity"] == "2kWh"
    assert calls == [True, False]
    refreshed_session = await service.cookie_cache.get(account.cache_key)
    assert refreshed_session is not None
    assert refreshed_session.cookies == {"sid": "fresh"}


@pytest.mark.asyncio
async def test_cookie_cache_isolated_by_key_and_copies_values():
    cache = InMemoryCookieCache(ttl_seconds=30)
    await cache.set("account-a", PortalSession({"sid": "a"}, "csrf-a"))
    await cache.set("account-b", PortalSession({"sid": "b"}, "csrf-b"))

    account_a_session = await cache.get("account-a")
    account_b_session = await cache.get("account-b")
    assert account_a_session is not None
    assert account_b_session is not None
    assert account_a_session.cookies == {"sid": "a"}
    assert account_b_session.cookies == {"sid": "b"}
    account_a_session.cookies["sid"] = "modified-copy"
    copied_session = await cache.get("account-a")
    assert copied_session is not None
    assert copied_session.cookies == {"sid": "a"}


@pytest.mark.asyncio
async def test_cookie_cache_expires_entries(monkeypatch):
    import services.cookie_cache as cookie_cache_module

    current_time = 10.0
    monkeypatch.setattr(cookie_cache_module.time, "monotonic", lambda: current_time)
    cache = InMemoryCookieCache(ttl_seconds=5)
    await cache.set("account", PortalSession({"sid": "a"}, "csrf"))
    current_time = 16.0

    assert await cache.get("account") is None


@pytest.mark.asyncio
async def test_account_pool_reports_missing_accounts_without_secret_details(tmp_path):
    _write_accounts(tmp_path / "accounts.json")
    service = ElectricityService(
        "https://payment.example", ElectricityAccountPool(tmp_path / "accounts.json")
    )

    with pytest.raises(AccountPoolConfigurationError, match="没有可用账号"):
        await service.query("06417", "hanpu")


@pytest.mark.asyncio
async def test_service_returns_safe_error_when_every_account_fails(tmp_path):
    _write_accounts(tmp_path / "accounts.json", ("account", "secret"))

    class FailedClient:
        def __init__(self, _base_url: str):
            pass

        async def query(self, **_kwargs):
            raise RuntimeError("remote exception contains internal details")

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        client_factory=FailedClient,
    )

    with pytest.raises(ElectricityQueryError) as error:
        await service.query("06417", "hanpu")
    assert "internal details" not in str(error.value)


@pytest.mark.asyncio
async def test_service_returns_safe_platform_error_when_every_account_fails(tmp_path):
    _write_accounts(tmp_path / "accounts.json", ("account", "secret"))

    class FailedClient:
        def __init__(self, _base_url: str):
            pass

        async def query(self, **_kwargs):
            raise ElectricityPlatformError("电费平台登录失败")

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        client_factory=FailedClient,
    )

    with pytest.raises(ElectricityQueryError, match="电费平台登录失败"):
        await service.query("06417", "hanpu")


@pytest.mark.asyncio
async def test_daily_reading_cache_avoids_repeating_platform_request(tmp_path):
    _write_accounts(tmp_path / "accounts.json", ("account", "secret"))
    calls = 0

    class FakeRedis:
        def __init__(self):
            self.values = {}

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, *, ex):
            assert ex > 0
            self.values[key] = value

    class FakeClient:
        def __init__(self, _base_url):
            pass

        async def query(self, room_number, campus, username, password, session=None):
            nonlocal calls
            calls += 1
            return (
                {"campus": campus, "room_number": room_number, "balance": 10.0},
                PortalSession({"sid": "session"}, "csrf"),
            )

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        client_factory=FakeClient,
        reading_cache=DailyElectricityCache(redis_client=FakeRedis()),
    )

    assert await service.query("06417", "hanpu") == await service.query(
        "06417", "hanpu"
    )
    assert calls == 1

    await service.query_live("06417", "hanpu")
    assert calls == 2


@pytest.mark.asyncio
async def test_cached_room_reading_never_queries_portal(tmp_path):
    catalog_path = tmp_path / "rooms.json"
    catalog_path.write_text(
        json.dumps(
            {
                "rooms": [
                    {
                        "campus": "campus-hanpu",
                        "campus_name": "含浦学生宿舍",
                        "room_number": "06417",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeCache:
        async def get(self, campus, room_number):
            assert (campus, room_number) == ("campus-hanpu", "06417")
            return {"campus": campus, "room_number": room_number, "balance": 5}

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        reading_cache=FakeCache(),
    )
    service.catalog_path = catalog_path

    assert await service.get_cached_room_reading("06417", "campus-hanpu") == (
        True,
        {"campus": "campus-hanpu", "room_number": "06417", "balance": 5},
    )
    assert await service.get_cached_room_reading("06418", "campus-hanpu") == (
        False,
        None,
    )


@pytest.mark.asyncio
async def test_cached_room_reading_resolves_hanpu_to_catalog_area_id(tmp_path):
    catalog_path = tmp_path / "rooms.json"
    catalog_path.write_text(
        json.dumps(
            {
                "rooms": [
                    {
                        "campus": "001000000007",
                        "campus_name": "含浦学生宿舍",
                        "room_number": "06417",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeCache:
        async def get(self, campus, room_number):
            assert (campus, room_number) == ("001000000007", "06417")
            return {"campus": campus, "room_number": room_number}

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        reading_cache=FakeCache(),
    )
    service.catalog_path = catalog_path

    assert await service.get_cached_room_reading("06417", "hanpu") == (
        True,
        {"campus": "001000000007", "room_number": "06417"},
    )


@pytest.mark.asyncio
async def test_service_detects_an_entirely_missing_current_day_cache(tmp_path):
    catalog_path = tmp_path / "rooms.json"
    catalog_path.write_text(
        json.dumps(
            {
                "rooms": [
                    {
                        "campus": "campus-hanpu",
                        "campus_name": "含浦学生宿舍",
                        "room_number": "06417",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeCache:
        def __init__(self, value):
            self.value = value

        async def get(self, _campus, _room_number):
            return self.value

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        reading_cache=FakeCache(None),
    )
    service.catalog_path = catalog_path

    assert await service.needs_today_collection(scheduled_time=time(0))

    service.reading_cache = FakeCache({"remaining_electricity": "5kWh"})
    assert not await service.needs_today_collection(scheduled_time=time(0))


@pytest.mark.asyncio
async def test_forced_collection_bypasses_the_daily_cache(tmp_path):
    catalog_path = tmp_path / "rooms.json"
    catalog_path.write_text(
        json.dumps(
            {
                "rooms": [
                    {
                        "campus": "campus-hanpu",
                        "campus_name": "含浦学生宿舍",
                        "room_number": "06417",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeCache:
        def __init__(self):
            self.values = {
                ("campus-hanpu", "06417"): {"remaining_electricity": "stale"}
            }

        async def get(self, campus, room_number):
            return self.values.get((campus, room_number))

        async def set(self, campus, room_number, value):
            self.values[(campus, room_number)] = value

    cache = FakeCache()
    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        reading_cache=cache,
    )
    service.catalog_path = catalog_path

    async def query_live(room_number, campus):
        assert (room_number, campus) == ("06417", "campus-hanpu")
        return {"remaining_electricity": "fresh"}

    service.query_live = query_live

    assert await service.collect_room_readings(
        batch_size=1, interval_seconds=0.001, force=True
    ) == {"succeeded": 1, "failed": 0}
    assert await cache.get("campus-hanpu", "06417") == {
        "remaining_electricity": "fresh"
    }


def test_daily_collection_status_reports_completion_for_todays_full_pass(tmp_path):
    service = ElectricityService(
        "https://payment.example", ElectricityAccountPool(tmp_path / "accounts.json")
    )
    service.collection_state_path = tmp_path / "collection-state.json"
    service.collection_state_path.write_text(
        json.dumps(
            {
                "date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                "next_index": 2,
                "total": 3,
            }
        ),
        encoding="utf-8",
    )

    assert service.daily_collection_status() == {
        "collection_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "total": 3,
        "queried": 2,
        "completed": False,
    }

    service.collection_state_path.write_text(
        json.dumps(
            {
                "date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                "next_index": 3,
                "total": 3,
            }
        ),
        encoding="utf-8",
    )
    assert service.daily_collection_status()["completed"] is True


@pytest.mark.asyncio
async def test_retry_collection_queries_only_rooms_missing_from_daily_cache(tmp_path):
    catalog_path = tmp_path / "rooms.json"
    state_path = tmp_path / "collection-state.json"
    catalog_path.write_text(
        json.dumps(
            {
                "rooms": [
                    {
                        "campus": "campus-hanpu",
                        "campus_name": "含浦学生宿舍",
                        "room_number": room_number,
                    }
                    for room_number in ("06417", "06418")
                ]
            }
        ),
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps(
            {
                "date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                "next_index": 2,
                "total": 2,
            }
        ),
        encoding="utf-8",
    )

    class FakeCache:
        async def get(self, _campus, room_number):
            return {"remaining_electricity": "5kWh"} if room_number == "06417" else None

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        reading_cache=FakeCache(),
    )
    service.catalog_path = catalog_path
    service.collection_state_path = state_path
    queried: list[str] = []

    async def query(room_number, _campus):
        queried.append(room_number)
        return {"room_number": room_number}

    service.query = query

    assert await service.collect_room_readings(
        batch_size=1, interval_seconds=0.001, retry_missing=True
    ) == {"succeeded": 1, "failed": 0}
    assert queried == ["06418"]
    assert json.loads(state_path.read_text(encoding="utf-8"))["next_index"] == 2


@pytest.mark.asyncio
async def test_collection_resumes_from_persisted_batch_checkpoint(tmp_path):
    catalog_path = tmp_path / "rooms.json"
    state_path = tmp_path / "collection-state.json"
    catalog_path.write_text(
        json.dumps(
            {
                "rooms": [
                    {
                        "campus": "campus-hanpu",
                        "campus_name": "含浦学生宿舍",
                        "room_number": room_number,
                    }
                    for room_number in ("06417", "06418", "06419")
                ]
            }
        ),
        encoding="utf-8",
    )
    service = ElectricityService(
        "https://payment.example", ElectricityAccountPool(tmp_path / "accounts.json")
    )
    service.catalog_path = catalog_path
    service.collection_state_path = state_path
    queried: list[str] = []

    async def interrupted_query(room_number, _campus):
        queried.append(room_number)
        if room_number == "06419":
            raise asyncio.CancelledError
        return {"room_number": room_number}

    service.query = interrupted_query
    with pytest.raises(asyncio.CancelledError):
        await service.collect_room_readings(batch_size=2, interval_seconds=0.001)
    assert json.loads(state_path.read_text(encoding="utf-8"))["next_index"] == 2

    resumed: list[str] = []

    async def resumed_query(room_number, _campus):
        resumed.append(room_number)
        return {"room_number": room_number}

    service.query = resumed_query
    result = await service.resume_today_collection()
    assert result == {"succeeded": 1, "failed": 0}
    assert resumed == ["06419"]


@pytest.mark.asyncio
async def test_daily_reading_cache_falls_back_to_memory_when_redis_fails():
    from redis.exceptions import ConnectionError

    class UnavailableRedis:
        async def get(self, _key, /):
            raise ConnectionError("unavailable")

        async def set(self, _key, _value, /, *, ex):
            raise ConnectionError("unavailable")

    cache = DailyElectricityCache(redis_client=UnavailableRedis())
    value = {"campus": "hanpu", "room_number": "06417", "balance": 10.0}

    assert await cache.get("hanpu", "06417") is None
    await cache.set("hanpu", "06417", value)
    cached_value = await cache.get("hanpu", "06417")

    assert cached_value == value
    assert cached_value is not value


@pytest.mark.parametrize(
    "building,room,expected",
    [
        ("东塘8号公寓", "417房", "08417"),
        ("6号公寓", "417房", "06417"),
        ("东塘国教7栋", "101房", "guojiao-07101"),
        ("东塘7号公寓", "104A房", None),
        ("请选择", "101房", None),
    ],
)
def test_room_labels_include_dongtang_without_ambiguous_buildings(
    building, room, expected
):
    assert HNUCMElectricityClient._room_number_from_options(building, room) == expected


@pytest.mark.parametrize("label", ["4楼", "4层", "6栋4层"])
def test_floor_matches_both_campus_formats(label):
    assert HNUCMElectricityClient._level_option_matches({"label": label}, "4")
    assert not HNUCMElectricityClient._level_option_matches({"label": "14楼"}, "4")


def test_existing_catalog_normalizes_dongtang_labels(tmp_path):
    path = tmp_path / "rooms.json"
    path.write_text(
        json.dumps(
            {
                "rooms": [
                    {
                        "campus": "east",
                        "campus_name": "东塘学生宿舍",
                        "building": "东塘8号公寓",
                        "level": "4楼",
                        "room": "417房",
                    },
                    {
                        "campus": "east",
                        "campus_name": "东塘学生宿舍",
                        "building": "东塘国教7栋",
                        "level": "1楼",
                        "room": "101房",
                        "room_number": "07101",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    service = ElectricityService(
        "https://payment.example", ElectricityAccountPool(tmp_path / "accounts.json")
    )
    service.catalog_path = path
    rooms = service.get_room_catalog()["rooms"]
    assert rooms[0]["room_number"] == "08417"
    assert rooms[1]["room_number"] == "guojiao-07101"
