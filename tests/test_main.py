from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import main
from main import (
    app,
    get_academic_account_pool,
    get_electricity_account_pool,
    get_electricity_service,
)
from services.electricity import AcademicAccountPool, ElectricityAccountPool


@pytest.fixture(autouse=True)
def internal_api_test_token(monkeypatch):
    """All API tests act as the trusted in-cluster caller."""
    monkeypatch.setenv("CRAWLER_INTERNAL_TOKEN", "test-token")
    original_client = httpx.AsyncClient

    class InternalClient(original_client):
        def __init__(self, *args, **kwargs):
            headers = dict(kwargs.pop("headers", {}))
            headers.setdefault("Authorization", "Bearer test-token")
            super().__init__(*args, headers=headers, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", InternalClient)


def override_electricity_service(service):
    async def override():
        return service

    app.dependency_overrides[get_electricity_service] = override


def override_electricity_account_pool(pool):
    async def override():
        return pool

    app.dependency_overrides[get_electricity_account_pool] = override


def override_academic_account_pool(pool):
    async def override():
        return pool

    app.dependency_overrides[get_academic_account_pool] = override


@pytest.mark.asyncio
async def test_electricity_account_pool_crud_endpoint_hides_password(tmp_path):
    accounts_file = tmp_path / "accounts.json"
    accounts_file.write_text(
        '{"accounts": [{"xh": "existing", "pwd": "secret"}]}', encoding="utf-8"
    )
    override_electricity_account_pool(ElectricityAccountPool(accounts_file))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/electricity/accounts")
            assert response.status_code == 200
            assert response.json() == {"accounts": [{"xh": "existing"}]}

            response = await client.post(
                "/api/v1/electricity/accounts", json={"xh": "new", "pwd": "new-secret"}
            )
            assert response.status_code == 201
            assert response.json() == {"xh": "new"}

            response = await client.put(
                "/api/v1/electricity/accounts/new",
                json={"xh": "renamed", "pwd": "changed"},
            )
            assert response.status_code == 200
            assert response.json() == {"xh": "renamed"}

            response = await client.delete("/api/v1/electricity/accounts/renamed")
            assert response.status_code == 204

            response = await client.get("/api/v1/electricity/accounts")
            assert response.json() == {"accounts": [{"xh": "existing"}]}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_academic_account_pool_crud_endpoint_is_separate(tmp_path):
    accounts_file = tmp_path / "academic-accounts.json"
    override_academic_account_pool(AcademicAccountPool(accounts_file))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/academic/accounts", json={"xh": "academic", "pwd": "secret"}
            )
            assert response.status_code == 201
            assert response.json() == {"xh": "academic"}

            response = await client.get("/api/v1/academic/accounts")
            assert response.json() == {"accounts": [{"xh": "academic"}]}

            response = await client.delete("/api/v1/academic/accounts/academic")
            assert response.status_code == 204
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_crawl_uses_academic_pool_when_credentials_are_omitted(
    monkeypatch, tmp_path
):
    accounts_file = tmp_path / "academic-accounts.json"
    accounts_file.write_text(
        '{"accounts": [{"xh": "pool-user", "pwd": "pool-password"}]}',
        encoding="utf-8",
    )
    override_academic_account_pool(AcademicAccountPool(accounts_file))
    adapter = AsyncMock()
    adapter.login.return_value = (True, "Success", object())
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/academic", json={"school": "HNUCM", "action": "login"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    adapter.login.assert_awaited_once_with("pool-user", "pool-password")


@pytest.mark.asyncio
async def test_health_is_available():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_electricity_room_catalog_endpoint_returns_saved_catalog(tmp_path):
    service = AsyncMock()
    service.get_room_catalog.return_value = {
        "generated_at": "2026-09-29T00:00:00+00:00",
        "rooms": [
            {
                "campus": "campus-hanpu",
                "campus_name": "含浦学生宿舍",
                "room_number": "06417",
            }
        ],
    }
    service.get_room_catalog = Mock(return_value=service.get_room_catalog.return_value)
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/electricity/rooms")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["rooms"][0]["campus"] == "campus-hanpu"


@pytest.mark.asyncio
async def test_electricity_room_catalog_refresh_endpoint_requires_token(monkeypatch):
    monkeypatch.setenv("CRAWLER_INTERNAL_TOKEN", "test-token")
    service = AsyncMock()
    service.refresh_room_catalog.return_value = {
        "generated_at": "2026-09-29T00:00:00+00:00",
        "rooms": [],
    }
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.post(
                "/api/v1/electricity/rooms/refresh",
                headers={"Authorization": "Bearer invalid"},
            )
            response = await client.post(
                "/api/v1/electricity/rooms/refresh",
                headers={"Authorization": "Bearer test-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 401
    assert response.status_code == 200
    service.refresh_room_catalog.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_electricity_endpoint_reads_cached_reading_only():
    service = AsyncMock()
    service.get_cached_room_reading.return_value = (
        True,
        {
            "campus": "campus-hanpu",
            "room_number": "06417",
            "remaining_electricity": "5kWh",
        },
    )
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/electricity/campus-hanpu/06417")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["remaining_electricity"] == "5kWh"
    service.get_cached_room_reading.assert_awaited_once_with("06417", "campus-hanpu")


@pytest.mark.asyncio
async def test_electricity_endpoint_returns_room_data():
    service = AsyncMock()
    service.get_cached_room_reading.return_value = (True, {
        "campus": "hanpu",
        "room_number": "06417",
        "name": "6号公寓417房",
        "meter_number": "meter-1",
        "remaining_electricity": "193.17kWh",
        "balance": 119.57,
        "state": "在线",
        "category": "ElecRoomYun",
    })
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/electricity/hanpu/06417")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["room_number"] == "06417"
    assert response.json()["remaining_electricity"] == "193.17kWh"
    service.get_cached_room_reading.assert_awaited_once_with("06417", "hanpu")


@pytest.mark.asyncio
async def test_electricity_endpoint_pads_four_digit_room_number():
    service = AsyncMock()
    service.get_cached_room_reading.return_value = (True, {"campus": "hanpu", "room_number": "06417"})
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/electricity/hanpu/6417")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["room_number"] == "06417"
    service.get_cached_room_reading.assert_awaited_once_with("06417", "hanpu")


@pytest.mark.asyncio
@pytest.mark.parametrize("room_number", ["6-417", "06-417"])
async def test_electricity_endpoint_normalizes_hyphenated_room_number(room_number):
    service = AsyncMock()
    service.get_cached_room_reading.return_value = (True, {"campus": "hanpu", "room_number": "06417"})
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/electricity/hanpu/{room_number}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["room_number"] == "06417"
    service.get_cached_room_reading.assert_awaited_once_with("06417", "hanpu")


@pytest.mark.asyncio
async def test_electricity_endpoint_reports_pending_daily_collection():
    service = AsyncMock()
    service.get_cached_room_reading.return_value = (True, None)
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/electricity/hanpu/06417")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == "该寝室今日电费尚未采集完成"


@pytest.mark.asyncio
async def test_electricity_endpoint_reports_invalid_room():
    service = AsyncMock()
    service.get_cached_room_reading.return_value = (False, None)
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/electricity/hanpu/06417")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "校区或寝室号码不存在"}
    service.get_cached_room_reading.assert_awaited_once_with("06417", "hanpu")


@pytest.mark.asyncio
@pytest.mark.parametrize("room_number", ["abcde", "123", "123456", "12-45", "123-456"])
async def test_electricity_endpoint_rejects_malformed_room_number(room_number):
    service = AsyncMock()
    override_electricity_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/electricity/hanpu/{room_number}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    service.get_cached_room_reading.assert_not_awaited()


@pytest.mark.asyncio
async def test_crawl_endpoint_keeps_login_operation(monkeypatch):
    adapter = AsyncMock()
    adapter.login.return_value = (True, "Success", object())
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/academic",
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
            "/api/v1/academic",
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
            "/api/v1/academic",
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
            "/api/v1/academic",
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
        response = await client.post("/api/v1/academic", json=request)

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
            "/api/v1/academic",
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
        getattr(adapter, method_name).assert_awaited_once_with(cookies, "student", "")
