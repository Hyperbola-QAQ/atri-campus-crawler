"""重号寝室的解析、目录与缓存隔离回归。"""

import json
from unittest.mock import AsyncMock

import pytest
import httpx

from adapter.hnucm_adapter.electricity import (
    ElectricityPlatformError,
    HNUCMElectricityClient,
)
from services.electricity import ElectricityAccountPool, ElectricityService
from services.electricity_cache import DailyElectricityCache
from utils.electricity_identity import normalize_room_number


@pytest.mark.parametrize(
    "campus,canonical,label",
    [
        ("hanpu", "campus-hanpu", "含浦学生宿舍"),
        ("含浦", "campus-hanpu", "含浦学生宿舍"),
        ("campus-hanpu", "campus-hanpu", "含浦学生宿舍"),
        ("dongtang", "campus-dongtang", "东塘学生宿舍"),
        ("东塘", "campus-dongtang", "东塘学生宿舍"),
        ("campus-dongtang", "campus-dongtang", "东塘学生宿舍"),
    ],
)
@pytest.mark.parametrize("room", ["6417", "6-417"])
async def test_campus_alias_api_reads_canonical_cache(
    tmp_path, campus, canonical, label, room
):
    from main import app, get_electricity_service

    cache = type(
        "Cache",
        (),
        {
            "get": AsyncMock(
                return_value={
                    "campus": canonical,
                    "room_number": "06417",
                    "balance": 34.88,
                }
            )
        },
    )()
    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        reading_cache=cache,
    )
    service.catalog_path = tmp_path / "rooms.json"
    service.catalog_path.write_text(
        json.dumps(
            {
                "rooms": [
                    {
                        "campus": canonical,
                        "campus_name": label,
                        "building": "6号公寓",
                        "room": "417房",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    async def override_service():
        return service

    app.dependency_overrides[get_electricity_service] = override_service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/electricity/{campus}/{room}")
        assert response.status_code == 200, response.text
        assert response.json()["campus"] == canonical
        assert response.json()["balance"] == 34.88
        cache.get.assert_awaited_once_with(canonical, "06417")
    finally:
        app.dependency_overrides.pop(get_electricity_service, None)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("7101", "07101"),
        ("7-101", "07101"),
        ("07101", "07101"),
        ("国教7-101", "guojiao-07101"),
        ("国教07101", "guojiao-07101"),
        ("guojiao-7-101", "guojiao-07101"),
        ("guojiao-07101", "guojiao-07101"),
    ],
)
def test_canonical_identity_preserves_building_category(value, expected):
    assert normalize_room_number(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "guojiao-",
        "guojiao-guojiao-07101",
        "国教国教07101",
        "guojiao-0007101",
        "../07101",
        "０７１０１",
        "07101\n",
    ],
)
def test_invalid_identity_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_room_number(value)


@pytest.mark.parametrize(
    "number,expected", [("07101", "ordinary"), ("guojiao-07101", "national")]
)
async def test_selector_uses_exact_building_category(monkeypatch, number, expected):
    client = HNUCMElectricityClient()

    async def options(_client, _token, key, selection):
        return {
            "area": [{"label": "东塘学生宿舍", "value": "east"}],
            "build": [
                {"label": "东塘7号公寓", "value": "ordinary"},
                {"label": "东塘国教7栋", "value": "national"},
            ],
            "level": [{"label": "1楼", "value": "floor-1"}],
            "room": [{"label": "101房", "value": f"{selection['buildid']}-101"}],
        }[key]

    monkeypatch.setattr(client, "_get_room_options", options)
    result = await client._resolve_room_selections(None, "token", number, "dongtang")
    assert len(result) == 1
    assert result[0]["buildid"] == expected
    assert result[0]["roomid"] == f"{expected}-101"


async def test_unresolved_duplicate_identity_never_chooses_first(monkeypatch):
    client = HNUCMElectricityClient()

    async def options(_client, _token, key, _selection):
        return {
            "area": [{"label": "东塘学生宿舍", "value": "east"}],
            "build": [
                {"label": "东塘国教7栋", "value": "first"},
                {"label": "东塘国教7栋", "value": "second"},
            ],
            "level": [{"label": "1楼", "value": "floor-1"}],
            "room": [{"label": "101房", "value": "101"}],
        }[key]

    monkeypatch.setattr(client, "_get_room_options", options)
    with pytest.raises(ElectricityPlatformError, match="多个同名寝室"):
        await client._resolve_room_selections(None, "token", "guojiao-07101", "east")


async def test_cache_and_catalog_keep_same_number_rooms_separate(tmp_path):
    redis = type(
        "Redis", (), {"get": AsyncMock(return_value=None), "set": AsyncMock()}
    )()
    cache = DailyElectricityCache(redis_client=redis)
    service = ElectricityService(
        "https://payment.example",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        reading_cache=cache,
    )
    service.catalog_path = tmp_path / "rooms.json"
    service.catalog_path.write_text(
        json.dumps(
            {
                "rooms": [
                    {
                        "campus": "east",
                        "campus_name": "东塘学生宿舍",
                        "building": building,
                        "level": "1楼",
                        "room": "101房",
                    }
                    for building in ("东塘7号公寓", "东塘国教7栋")
                ]
            }
        ),
        encoding="utf-8",
    )
    ordinary = {"campus": "east", "room_number": "07101", "balance": 10}
    national = {"campus": "east", "room_number": "guojiao-07101", "balance": 99}
    await cache.set("east", "07101", ordinary)
    assert await service.get_cached_room_reading("guojiao-07101", "dongtang") == (
        True,
        None,
    )
    await cache.set("east", "国教7-101", national)
    assert await service.get_cached_room_reading("07101", "dongtang") == (
        True,
        ordinary,
    )
    assert await service.get_cached_room_reading("guojiao-07101", "dongtang") == (
        True,
        national,
    )
    assert redis.set.call_args_list[0].args[0] != redis.set.call_args_list[1].args[0]
    assert await service.get_cached_room_reading("guojiao-07101", "hanpu") == (
        False,
        None,
    )


async def test_cache_rejects_payload_from_another_building():
    redis = type(
        "Redis",
        (),
        {
            "get": AsyncMock(
                return_value=json.dumps({"room_number": "07101", "balance": 100})
            ),
            "set": AsyncMock(),
        },
    )()
    cache = DailyElectricityCache(redis_client=redis)
    assert await cache.get("east", "guojiao-07101") is None
    with pytest.raises(ValueError, match="寝室身份不一致"):
        await cache.set(
            "east", "guojiao-07101", {"room_number": "07101", "balance": 100}
        )
    redis.set.assert_not_awaited()
