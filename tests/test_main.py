from unittest.mock import AsyncMock

import httpx
import pytest

import main
from main import app, get_electricity_service


def override_electricity_service(service):
    async def override():
        return service

    app.dependency_overrides[get_electricity_service] = override


@pytest.mark.asyncio
async def test_health_is_available():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_electricity_endpoint_returns_room_data():
    service = AsyncMock()
    service.query.return_value = {
        "room_number": "06417",
        "name": "6号公寓417房",
        "meter_number": "meter-1",
        "remaining_electricity": "193.17kWh",
        "balance": 119.57,
        "state": "在线",
        "category": "ElecRoomYun",
    }
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/electricity/06417")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["room_number"] == "06417"
    assert response.json()["remaining_electricity"] == "193.17kWh"
    service.query.assert_awaited_once_with("06417")


@pytest.mark.asyncio
async def test_electricity_endpoint_pads_four_digit_room_number():
    service = AsyncMock()
    service.query.return_value = {"room_number": "06417"}
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/electricity/6417")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["room_number"] == "06417"
    service.query.assert_awaited_once_with("06417")


@pytest.mark.asyncio
@pytest.mark.parametrize("room_number", ["6-417", "06-417"])
async def test_electricity_endpoint_normalizes_hyphenated_room_number(room_number):
    service = AsyncMock()
    service.query.return_value = {"room_number": "06417"}
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/electricity/{room_number}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["room_number"] == "06417"
    service.query.assert_awaited_once_with("06417")


@pytest.mark.asyncio
async def test_electricity_endpoint_reports_query_failure():
    from services.electricity import ElectricityQueryError

    service = AsyncMock()
    service.query.side_effect = ElectricityQueryError("查询失败")
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/electricity/06417")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["detail"] == "查询失败"


@pytest.mark.asyncio
async def test_electricity_endpoint_reports_configuration_failure():
    from services.electricity import AccountPoolConfigurationError

    service = AsyncMock()
    service.query.side_effect = AccountPoolConfigurationError("未配置电费平台地址")
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/electricity/06417")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "未配置电费平台地址"}
    service.query.assert_awaited_once_with("06417")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "room_number", ["abcde", "123", "123456", "12-45", "123-456"]
)
async def test_electricity_endpoint_rejects_malformed_room_number(room_number):
    service = AsyncMock()
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/electricity/{room_number}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    service.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_crawl_endpoint_keeps_login_operation(monkeypatch):
    adapter = AsyncMock()
    adapter.login.return_value = (True, "Success", object())
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/crawl",
            json={
                "school": "HNUCM",
                "action": "login",
                "username": "student",
                "password": "password",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "data": {"success": True},
        "error": None,
    }
    adapter.login.assert_awaited_once_with("student", "password")


@pytest.mark.asyncio
async def test_crawl_endpoint_returns_serializable_profile(monkeypatch):
    profile = type("ProfileValue", (), {"model_dump": lambda self: {"name": "示例"}})()
    adapter = AsyncMock()
    adapter.login.return_value = (True, "Success", object())
    adapter.get_profile.return_value = (True, "Success", profile)
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/crawl",
            json={
                "school": "HNUCM",
                "action": "get_profile",
                "username": "student",
                "password": "password",
            },
        )

    assert response.status_code == 200
    assert response.json()["data"] == {"name": "示例"}
    adapter.get_profile.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "method_name"),
    [("get_grades", "get_grades"), ("get_course_schedule", "get_course_schedule")],
)
async def test_crawl_endpoint_forwards_semester_and_serializes_lists(
    monkeypatch, action, method_name
):
    cookies = object()
    item = type("DataItem", (), {"model_dump": lambda self: {"item": "value"}})()
    adapter = AsyncMock()
    adapter.login.return_value = (True, "Success", cookies)
    getattr(adapter, method_name).return_value = (True, "Success", [item])
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/crawl",
            json={
                "school": "HNUCM",
                "action": action,
                "username": "student",
                "password": "password",
                "params": {"semester": "2023-2024-1"},
            },
        )

    assert response.status_code == 200
    assert response.json()["data"] == [{"item": "value"}]
    getattr(adapter, method_name).assert_awaited_once_with(
        cookies, "student", "2023-2024-1"
    )


@pytest.mark.asyncio
async def test_crawl_endpoint_reports_login_failure(monkeypatch):
    adapter = AsyncMock()
    adapter.login.return_value = (False, "登录失败", None)
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/crawl",
            json={
                "school": "HNUCM",
                "action": "login",
                "username": "student",
                "password": "password",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "登录失败"
    adapter.get_profile.assert_not_awaited()
    adapter.get_grades.assert_not_awaited()
    adapter.get_course_schedule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"school": "UNKNOWN"},
        {"action": "unknown"},
        {"username": ""},
        {"password": ""},
    ],
)
async def test_crawl_endpoint_rejects_invalid_request(monkeypatch, invalid_fields):
    adapter = AsyncMock()
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)
    request = {
        "school": "HNUCM",
        "action": "login",
        "username": "student",
        "password": "password",
    }
    request.update(invalid_fields)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/crawl", json=request)

    assert response.status_code == 422
    adapter.login.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "method_name", "result"),
    [
        ("get_profile", "get_profile", (False, "资料查询失败", None)),
        ("get_grades", "get_grades", (True, "无成绩数据", None)),
        ("get_course_schedule", "get_course_schedule", (False, "课表查询失败", [])),
    ],
)
async def test_crawl_endpoint_reports_action_failure(
    monkeypatch, action, method_name, result
):
    cookies = object()
    adapter = AsyncMock()
    adapter.login.return_value = (True, "Success", cookies)
    getattr(adapter, method_name).return_value = result
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/crawl",
            json={
                "school": "HNUCM",
                "action": action,
                "username": "student",
                "password": "password",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "failed", "data": None, "error": result[1]}
    if action == "get_profile":
        adapter.get_profile.assert_awaited_once_with(cookies, "student")
    else:
        getattr(adapter, method_name).assert_awaited_once_with(
            cookies, "student", ""
        )
