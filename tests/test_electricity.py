import json
from pathlib import Path

import httpx
import pytest

from adapter.hnucm_adapter.electricity import (
    ElectricityPlatformError,
    HNUCMElectricityClient,
    _jsbn_hex_to_base64,
    encrypt_password,
)
from services.cookie_cache import InMemoryCookieCache, PortalSession
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
                        "value": "room-417" if option["levelid"] == "level-4" else "room-418",
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
                        }
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

    result, session = await client.query("06417", "account", "password")
    cached_result, refreshed_session = await client.query(
        "06418", "account", "password", session=session
    )

    assert result == {
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
            username: str,
            password: str,
            session: PortalSession | None = None,
        ):
            attempted.append((username, session is not None))
            if username == "account-a":
                raise RuntimeError("login failed")
            assert password == "secret-b"
            return (
                {"room_number": room_number, "remaining_electricity": "5kWh"},
                PortalSession({"sid": username}, "csrf"),
            )

    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        client_factory=FakeClient,
    )

    first_result = await service.query("06417")
    second_result = await service.query("06418")

    assert first_result == {
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

        async def query(self, room_number, username, password, session=None):
            calls.append(session is not None)
            if session is not None:
                raise RuntimeError("session expired")
            return (
                {"room_number": room_number, "remaining_electricity": "2kWh"},
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

    result = await service.query("06417")

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
        await service.query("06417")


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
        await service.query("06417")
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
        await service.query("06417")
