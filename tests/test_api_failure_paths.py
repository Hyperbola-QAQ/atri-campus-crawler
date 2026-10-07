"""HTTP 错误映射、启动清理及教务返回值测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import main
from services.electricity import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    AccountPoolConfigurationError,
    ElectricityQueryError,
)


@pytest.mark.parametrize(
    "body",
    [
        {"username": "student"},
        {"password": "password"},
        {"params": {"student_id": 123}},
        {"params": {"student_id": " "}},
        {
            "username": "student",
            "password": "password",
            "params": {"student_id": "other"},
        },
    ],
)
def test_crawl_credentials_validation(body):
    with pytest.raises(ValidationError):
        main.CrawlRequest(action="login", **body)


@pytest.mark.parametrize("kind", ["electricity", "academic"])
@pytest.mark.parametrize(
    "operation,error,status",
    [
        ("list", AccountPoolConfigurationError, 503),
        ("create", AccountAlreadyExistsError, 409),
        ("create", AccountPoolConfigurationError, 503),
        ("update", AccountNotFoundError, 404),
        ("update", AccountAlreadyExistsError, 409),
        ("update", AccountPoolConfigurationError, 503),
        ("delete", AccountNotFoundError, 404),
        ("delete", AccountPoolConfigurationError, 503),
    ],
)
async def test_account_error_http_mapping(monkeypatch, kind, operation, error, status):
    pool = SimpleNamespace(
        **{
            name: AsyncMock(side_effect=error("safe failure"))
            for name in (
                "list_accounts",
                "add_account",
                "update_account",
                "delete_account",
            )
        }
    )
    dependency = (
        main.get_electricity_account_pool
        if kind == "electricity"
        else main.get_academic_account_pool
    )

    async def override():
        return pool

    main.app.dependency_overrides[dependency] = override
    monkeypatch.setenv("CRAWLER_INTERNAL_TOKEN", "test")
    method, suffix, body = {
        "list": ("GET", "", None),
        "create": ("POST", "", {"xh": "student", "pwd": "password"}),
        "update": ("PUT", "/student", {"pwd": "new"}),
        "delete": ("DELETE", "/student", None),
    }[operation]
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://test",
            headers={"Authorization": "Bearer test"},
        ) as client:
            response = await client.request(
                method, f"/api/v1/{kind}/accounts{suffix}", json=body
            )
        assert response.status_code == status
        assert response.json() == {"detail": "safe failure"}
    finally:
        main.app.dependency_overrides.pop(dependency)


@pytest.mark.parametrize("kind", ["electricity", "academic"])
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_account_school_mismatch(kind, operation):
    cls = getattr(
        main,
        ("Electricity" if kind == "electricity" else "Academic")
        + ("AccountCreate" if operation == "create" else "AccountUpdate"),
    )
    request = cls(school="HNUCM", xh="student", pwd="password")
    function = getattr(main, f"{operation}_{kind}_account")
    with pytest.raises(HTTPException) as error:
        if operation == "create":
            await function(request, account_pool=AsyncMock(), school="OTHER")
        else:
            await function("student", request, account_pool=AsyncMock(), school="OTHER")
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "error,status", [(AccountPoolConfigurationError, 503), (ElectricityQueryError, 502)]
)
async def test_catalog_refresh_errors(error, status):
    with pytest.raises(HTTPException) as caught:
        await main.refresh_electricity_rooms(
            SimpleNamespace(refresh_room_catalog=AsyncMock(side_effect=error("safe")))
        )
    assert caught.value.status_code == status


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("needs", [False, True])
async def test_initial_sync(monkeypatch, failed, needs):
    service = SimpleNamespace(
        refresh_room_catalog=AsyncMock(
            side_effect=ElectricityQueryError("offline") if failed else None
        ),
        needs_today_collection=AsyncMock(return_value=needs),
        collect_room_readings=AsyncMock(
            side_effect=AccountPoolConfigurationError("offline") if failed else None
        ),
    )
    monkeypatch.setattr(
        main, "get_electricity_service", AsyncMock(return_value=service)
    )
    await main._initial_electricity_sync()
    assert service.collect_room_readings.await_count == int(needs)
    if needs:
        service.collect_room_readings.assert_awaited_once_with(force=True)


@pytest.mark.parametrize("complete", [False, True])
async def test_lifespan_stops_schedule_and_background_task(monkeypatch, complete):
    entered = asyncio.Event()
    finished = asyncio.Event()

    async def sync():
        entered.set()
        try:
            if not complete:
                await asyncio.Event().wait()
        finally:
            finished.set()

    schedule = SimpleNamespace(start=Mock(), stop=AsyncMock())
    monkeypatch.setattr(main, "_initial_electricity_sync", sync)
    monkeypatch.setattr(
        main, "get_electricity_service", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(main, "ElectricitySchedule", lambda service: schedule)
    application = SimpleNamespace(state=SimpleNamespace())
    async with main.lifespan(application):
        await entered.wait()
        schedule.start.assert_called_once()
    schedule.stop.assert_awaited_once()
    assert finished.is_set()
    assert application.state.electricity_catalog_task.done()
    assert application.state.electricity_catalog_task.cancelled() is not complete


@pytest.mark.parametrize(
    "action", ["login", "get_profile", "get_grades", "get_course_schedule"]
)
@pytest.mark.parametrize("mode", ["success", "login_failed", "data_failed", "missing"])
async def test_crawl_result_serialization(monkeypatch, action, mode):
    payload = main.CrawlResponse(status="success", data={"sample": 1})
    data = (
        [payload, {"plain": 2}]
        if action in {"get_grades", "get_course_schedule"}
        else payload
    )
    if mode == "missing":
        data = None
    adapter = SimpleNamespace(
        login=AsyncMock(
            return_value=(mode != "login_failed", "failure", httpx.Cookies())
        )
    )
    for method in ("get_profile", "get_grades", "get_course_schedule"):
        setattr(
            adapter,
            method,
            AsyncMock(return_value=(mode != "data_failed", "failure", data)),
        )
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)
    response, logged_in = await main._crawl_with_credentials(
        main.CrawlRequest(action=action), "student", "password"
    )
    assert logged_in is (mode != "login_failed")
    if mode == "login_failed":
        assert response.error_code == "ACADEMIC_LOGIN_FAILED"
    elif action == "login":
        assert response.data == {"success": True}
    elif mode in {"data_failed", "missing"}:
        assert response.status == "failed"
    else:
        assert response.data == (
            [payload.model_dump(), {"plain": 2}]
            if isinstance(data, list)
            else payload.model_dump()
        )


async def test_crawl_no_account_can_login(monkeypatch):
    pool = SimpleNamespace(
        rotated_accounts=AsyncMock(
            return_value=[SimpleNamespace(username="student", password="password")]
        )
    )
    monkeypatch.setattr(
        main,
        "_crawl_with_credentials",
        AsyncMock(return_value=(main.CrawlResponse(status="failed"), False)),
    )
    response = await main.crawl(main.CrawlRequest(action="login"), pool)
    assert response.status == "failed"
    assert response.error == "教务账号池中没有可登录账号"
    pool.rotated_accounts.side_effect = AccountPoolConfigurationError("offline")
    with pytest.raises(HTTPException) as caught:
        await main.crawl(main.CrawlRequest(action="login"), pool)
    assert caught.value.status_code == 503


async def test_default_service_dependencies(monkeypatch, tmp_path):
    monkeypatch.setenv("ELECTRICITY_ACCOUNTS_FILE", str(tmp_path / "accounts.json"))
    main._cached_electricity_service.cache_clear()
    try:
        service = await main.get_electricity_service()
        assert service is await main.get_electricity_service()
        assert await main.get_electricity_account_pool() is service.account_pool
    finally:
        main._cached_electricity_service.cache_clear()


def test_server_entrypoint(monkeypatch):
    run = Mock()
    monkeypatch.setattr(main.uvicorn, "run", run)
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setenv("API_PORT", "9000")
    monkeypatch.setenv("API_RELOAD", "true")
    main.main()
    run.assert_called_once_with("main:app", host="127.0.0.1", port=9000, reload=True)
