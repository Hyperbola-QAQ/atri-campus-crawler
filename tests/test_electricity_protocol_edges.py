"""电费门户协议校验、失败响应和残缺目录测试。"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from adapter.hnucm_adapter import electricity as module
from adapter.hnucm_adapter.electricity import (
    ElectricityPlatformError,
    HNUCMElectricityClient,
)
from services.cookie_cache import PortalSession


@pytest.mark.parametrize("key", ["not-hex", "00", "f", "ff"])
def test_invalid_rsa_keys_and_short_modulus(key):
    with pytest.raises(ElectricityPlatformError):
        module.encrypt_password("password", key, "random")


@pytest.mark.parametrize("html", ["", "<html></html>", '<input id="pbk" value="">'])
def test_login_missing_hidden_fields(html):
    with pytest.raises(ElectricityPlatformError):
        module._hidden_value(html, "pbk")


def test_invalid_response_json():
    with pytest.raises(ElectricityPlatformError, match="无法识别"):
        module._response_json(httpx.Response(200, text="html"))


@pytest.mark.parametrize("room,url", [("bad", "https://example.test"), ("06417", "")])
async def test_query_invalid_configuration(room, url):
    with pytest.raises(ElectricityPlatformError):
        await HNUCMElectricityClient(url).query(room, "hanpu", "student", "password")


@pytest.mark.parametrize(
    "method,match",
    [
        ("_get_csrf_token", "无法连接"),
        ("_get_login_parameters", "无法读取"),
        ("_login", "登录请求失败"),
        ("_get_room_options", "无法读取"),
        ("_get_electricity_for_selection", "查询请求失败"),
    ],
)
@pytest.mark.parametrize("failure", ["network", "http"])
async def test_portal_http_failures(method, match, failure):
    response = httpx.Response(503, request=httpx.Request("GET", "https://example.test"))
    kwargs = dict(
        return_value=response,
        side_effect=httpx.ConnectError("offline") if failure == "network" else None,
    )
    client = AsyncMock()
    client.post = AsyncMock(**kwargs)
    client.get = AsyncMock(**kwargs)
    portal = HNUCMElectricityClient()
    args = {
        "_get_csrf_token": (client,),
        "_get_login_parameters": (client,),
        "_login": (client, "csrf", ("f" * 256, "random"), "student", "password"),
        "_get_room_options": (client, "csrf", "area", {}),
        "_get_electricity_for_selection": (client, "csrf", "06417", "hanpu", {}),
    }[method]
    with pytest.raises(ElectricityPlatformError, match=match):
        await getattr(portal, method)(*args)


@pytest.mark.parametrize("token", [None, {}, ""])
async def test_invalid_csrf_token(token):
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        200,
        content=json.dumps(token),
        request=httpx.Request("POST", "https://example.test"),
    )
    with pytest.raises(ElectricityPlatformError, match="令牌"):
        await HNUCMElectricityClient()._get_csrf_token(client)


@pytest.mark.parametrize("result", [[], {}, {"code": "bad"}, {"code": "FIRST0001"}])
async def test_login_business_result(result):
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        200, json=result, request=httpx.Request("POST", "https://example.test")
    )
    portal = HNUCMElectricityClient()
    if result == {"code": "FIRST0001"}:
        await portal._login(
            client, "csrf", ("f" * 256, "random"), "student", "password"
        )
    else:
        with pytest.raises(ElectricityPlatformError, match="登录失败"):
            await portal._login(
                client, "csrf", ("f" * 256, "random"), "student", "password"
            )


@pytest.mark.parametrize(
    "result",
    [
        [],
        {},
        {"IsSuccess": False, "RetCode": "500", "RetMsg": ""},
        {"IsSuccess": True, "Content": {}},
        {"IsSuccess": True, "Content": [None, {"label": "room"}]},
    ],
)
async def test_room_options_payload_validation(result):
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        200, json=result, request=httpx.Request("POST", "https://example.test")
    )
    portal = HNUCMElectricityClient()
    if isinstance(result, dict) and isinstance(result.get("Content"), list):
        assert await portal._get_room_options(client, "csrf", "area", {}) == [
            {"label": "room"}
        ]
    else:
        with pytest.raises(ElectricityPlatformError):
            await portal._get_room_options(client, "csrf", "area", {})


@pytest.mark.parametrize(
    "result",
    [
        [],
        {"RetCode": "F"},
        {"Content": {"Succ": False, "CzThirdInfo": {"Balance": 1}}},
        {"Content": None},
    ],
)
async def test_meter_failure_and_exhausted_retry(monkeypatch, result):
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        200, json=result, request=httpx.Request("POST", "https://example.test")
    )
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    portal = HNUCMElectricityClient()
    if result == [] or result.get("RetCode") == "F":
        with pytest.raises(ElectricityPlatformError):
            await portal._get_electricity_for_selection(
                client, "csrf", "06417", "hanpu", {}
            )
        assert client.post.await_count == 1
    else:
        assert (
            await portal._get_electricity_for_selection(
                client, "csrf", "06417", "hanpu", {}
            )
            is None
        )
        assert client.post.await_count == (3 if result == {"Content": None} else 1)
        assert sleep.await_count == (2 if result == {"Content": None} else 0)


@pytest.mark.parametrize("meter", [[], {}, {"PackageName": "", "Balance": None}])
def test_meter_requires_usable_reading(meter):
    with pytest.raises(ElectricityPlatformError):
        HNUCMElectricityClient._serialize_result("06417", "hanpu", meter)


async def test_meter_no_selection_has_reading(monkeypatch):
    portal = HNUCMElectricityClient()
    monkeypatch.setattr(
        portal, "_resolve_room_selections", AsyncMock(return_value=[{}, {}])
    )
    monkeypatch.setattr(
        portal, "_get_electricity_for_selection", AsyncMock(return_value=None)
    )
    with pytest.raises(ElectricityPlatformError, match="电表信息"):
        await portal._get_electricity(None, "csrf", "06417", "hanpu")
    assert portal._get_electricity_for_selection.await_count == 2


async def test_discovery_reuses_session(monkeypatch):
    portal = HNUCMElectricityClient(
        "https://example.test", client_factory=httpx.AsyncClient
    )
    monkeypatch.setattr(portal, "_discover_room_options", AsyncMock(return_value=[]))
    monkeypatch.setattr(portal, "_login", AsyncMock())
    rooms, session = await portal.discover_rooms(
        "student", "password", PortalSession({"sid": "cached"}, "csrf")
    )
    assert rooms == []
    assert session.cookies == {"sid": "cached"}
    assert session.csrf_token == "csrf"
    portal._login.assert_not_awaited()


@pytest.mark.parametrize("failed_level", ["none", "build", "level", "room"])
async def test_discovery_skips_broken_selector_branches(monkeypatch, failed_level):
    portal = HNUCMElectricityClient()

    async def options(client, csrf, key, selection):
        if key == "area":
            return [
                {"value": None, "label": "含浦宿舍"},
                {"value": "merchant", "label": "商户宿舍"},
                {"value": "other", "label": "办公室"},
                {"value": "a", "label": "含浦宿舍"},
            ]
        if key == failed_level:
            raise ElectricityPlatformError("unavailable selector")
        return [
            {"value": None, "label": "invalid"},
            {
                "value": "valid",
                "label": {"build": "6号公寓", "level": "4层", "room": "417房"}[key],
            },
            *([{"value": "odd", "label": "104A房"}] if key == "room" else []),
        ]

    monkeypatch.setattr(portal, "_get_room_options", options)
    rooms = await portal._discover_room_options(None, "csrf")
    if failed_level == "none":
        assert len(rooms) == 2
        assert rooms[0]["room_number"] == "06417"
        assert "room_number" not in rooms[1]
    else:
        assert rooms == []


@pytest.mark.parametrize("label", ["100号公寓", "invalid"])
def test_invalid_building_number(label):
    assert HNUCMElectricityClient._room_number_from_options(label, "417房") is None


async def test_room_resolution_filters_wrong_options(monkeypatch):
    portal = HNUCMElectricityClient()

    async def options(client, csrf, key, selection):
        return {
            "area": [
                {"value": None},
                {"value": "-1"},
                {"value": "wrong", "label": "办公室"},
                {"value": "a", "label": "含浦宿舍"},
            ],
            "build": [
                {"value": None},
                {"value": "wrong", "label": "7号公寓"},
                {"value": "b", "label": "6号公寓"},
            ],
            "level": [
                {"value": None},
                {"value": "wrong", "label": "14层"},
                {"value": "l", "label": "4层"},
            ],
            "room": [
                {"value": None, "label": "417房"},
                {"value": "wrong", "label": "418房"},
                {"value": "r", "label": "417房"},
            ],
        }[key]

    monkeypatch.setattr(portal, "_get_room_options", options)
    selections = await portal._resolve_room_selections(None, "csrf", "06417", "hanpu")
    assert len(selections) == 1
    assert selections[0]["roomid"] == "r"
    with pytest.raises(ElectricityPlatformError, match="寝室标识"):
        await portal._resolve_room_selections(None, "csrf", "invalid", "hanpu")
    with pytest.raises(ElectricityPlatformError, match="未找到"):
        await portal._resolve_room_selections(None, "csrf", "06417", "unavailable")
