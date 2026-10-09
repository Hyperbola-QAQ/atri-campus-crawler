"""System contracts: bootstrap ownership, credential proof, recovery and resources."""

import json
import asyncio
import os
import stat
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import httpx
import pytest

import main
from adapter.hnucm_adapter import auth
from services.account_bootstrap import seed_account_pool
from services.cookie_cache import InMemoryCookieCache, PortalSession
from services.electricity import (
    AccountPoolConfigurationError,
    ElectricityAccountPool,
    ElectricityService,
)
from services.electricity_cache import DailyElectricityCache
from services.electricity_schedule import ElectricitySchedule
from utils import redis as redis_module


async def test_secret_seed_then_crud_preserves_changes_across_restart(tmp_path):
    source, destination = tmp_path / "secret.json", tmp_path / "state" / "accounts.json"
    source.write_text(json.dumps({"accounts": [{"xh": "one", "pwd": "original"}]}))
    assert seed_account_pool(source, destination)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    pool = ElectricityAccountPool(destination)
    await pool.add_account("two", "second")
    await pool.update_account("one", new_username=None, password="updated")
    await pool.delete_account("two")
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    source.write_text(json.dumps({"accounts": [{"xh": "seed-new", "pwd": "new"}]}))
    assert not seed_account_pool(source, destination)
    assert [(a.username, a.password) for a in await pool.rotated_accounts()] == [
        ("one", "updated")
    ]
    assert sorted(path.name for path in destination.parent.iterdir()) == [
        "accounts.json"
    ]


def test_bad_initial_secret_does_not_create_pool(tmp_path):
    source, destination = tmp_path / "secret.json", tmp_path / "state" / "accounts.json"
    source.write_text('{"accounts": [{"xh": "one"}]}')
    with pytest.raises(AccountPoolConfigurationError):
        seed_account_pool(source, destination)
    assert not destination.exists()


async def test_old_password_cache_is_not_credential_proof(monkeypatch):
    monkeypatch.setattr(auth, "is_in_maintenance_window", lambda: False)
    cached = httpx.Cookies()
    cached.set("sid", "still-live-after-password-change")
    cache_read = AsyncMock(return_value=cached)
    fresh = AsyncMock(side_effect=ValueError("password was revoked"))
    monkeypatch.setattr(auth, "get_cookies_from_redis", cache_read)
    monkeypatch.setattr(auth, "get_cookies_from_jwxt", fresh)
    with pytest.raises(ValueError, match="password was revoked"):
        await auth.get_valid_cookies(
            "https://example.test",
            "HNUCM",
            1,
            {},
            "student",
            "old-password",
            force_login=True,
        )
    cache_read.assert_not_awaited()
    fresh.assert_awaited_once()


@pytest.mark.parametrize("action,verify", [("login", False), ("get_profile", True)])
async def test_verification_requests_always_fresh_login(monkeypatch, action, verify):
    adapter = SimpleNamespace(
        login=AsyncMock(return_value=(False, "revoked password", httpx.Cookies())),
        get_profile=AsyncMock(),
    )
    monkeypatch.setitem(main.SCHOOL_ADAPTERS, "HNUCM", lambda: adapter)
    result, success = await main._crawl_with_credentials(
        main.CrawlRequest(action=action, verify_credentials=verify), "student", "old"
    )
    assert not success
    assert result.error_code == "ACADEMIC_LOGIN_FAILED"
    adapter.login.assert_awaited_once_with("student", "old", force_login=True)
    adapter.get_profile.assert_not_awaited()


@pytest.mark.parametrize("before_settlement", [False, True])
async def test_startup_has_one_owner_and_only_retries_missing(
    monkeypatch, before_settlement
):
    service = AsyncMock()
    service.needs_today_collection.return_value = not before_settlement
    monkeypatch.setattr(
        main, "get_electricity_service", AsyncMock(return_value=service)
    )
    await main._initial_electricity_sync()
    if before_settlement:
        service.resume_today_collection.assert_not_awaited()
        service.collect_room_readings.assert_not_awaited()
    else:
        assert service.mock_calls == [
            call.needs_today_collection(),
            call.resume_today_collection(),
            call.refresh_room_catalog(),
            call.needs_today_collection(),
            call.collect_room_readings(retry_missing=True),
        ]


async def test_startup_missing_retry_survives_resume_failure(monkeypatch):
    service = AsyncMock()
    service.needs_today_collection.return_value = True
    service.resume_today_collection.side_effect = RuntimeError("bad checkpoint")
    monkeypatch.setattr(
        main, "get_electricity_service", AsyncMock(return_value=service)
    )
    await main._initial_electricity_sync()
    service.collect_room_readings.assert_awaited_once_with(retry_missing=True)


async def test_shutdown_closes_owned_redis_pool_once(monkeypatch):
    client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(redis_module, "_REDIS_CLIENT", client)
    await redis_module.close_redis_client()
    await redis_module.close_redis_client()
    client.aclose.assert_awaited_once()
    assert redis_module._REDIS_CLIENT is None


async def test_cache_capacity_and_expired_cookie_cleanup(monkeypatch):
    cache = DailyElectricityCache(max_memory_entries=2)
    expires = datetime.now(cache._timezone) + timedelta(hours=1)
    for key in ("one", "two", "three"):
        await cache._memory_set(key, {"room_number": "1234"}, expires)
    assert list(cache._memory_entries) == ["two", "three"]

    clock = [100.0]
    monkeypatch.setattr("services.cookie_cache.time.monotonic", lambda: clock[0])
    cookies = InMemoryCookieCache(ttl_seconds=1, max_entries=2)
    session = PortalSession({"sid": "s"}, "csrf")
    await cookies.set("expired-unread", session)
    clock[0] += 2
    for key in ("one", "two", "three"):
        await cookies.set(key, session)
    assert list(cookies._entries) == ["two", "three"]


async def test_first_startup_retry_publishes_full_catalog_completion(
    tmp_path, monkeypatch
):
    service = ElectricityService(
        "https://example.test", ElectricityAccountPool(tmp_path / "accounts")
    )
    service.catalog_path = tmp_path / "catalog.json"
    service.collection_state_path = tmp_path / "state.json"
    service._write_room_catalog(
        {
            "rooms": [
                {"campus": "c", "campus_name": "学生宿舍", "room_number": "1234"},
                {"campus": "c", "campus_name": "学生宿舍", "room_number": "1235"},
            ]
        }
    )
    monkeypatch.setattr(
        service,
        "query_live",
        AsyncMock(side_effect=lambda room, campus: {"room_number": room}),
    )
    # One successful snapshot already exists, but no persisted full-pass state.
    await service.reading_cache.set("c", "1234", {"room_number": "1234"})
    assert not service.daily_collection_status()["completed"]
    await service.collect_room_readings(retry_missing=True, interval_seconds=0.001)
    service.query_live.assert_awaited_once_with("01235", "c")
    assert service.daily_collection_status() == {
        "collection_date": service._collection_date(),
        "total": 2,
        "queried": 2,
        "completed": True,
    }


async def test_jobs_wait_for_startup_and_shutdown_retains_ownership(monkeypatch):
    startup = asyncio.create_task(asyncio.Event().wait())
    job = AsyncMock()
    schedule = ElectricitySchedule(None)
    for name in (
        "_run_weekly_catalog_refresh",
        "_run_daily_reading_collection",
        "_retry_failed_daily_collection",
    ):
        monkeypatch.setattr(schedule, name, job)
    schedule.start(startup_task=startup)
    await asyncio.sleep(0)
    job.assert_not_awaited()
    await schedule.stop()
    assert not startup.done()
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup


async def test_jobs_start_after_initial_recovery_finishes(monkeypatch):
    ready = asyncio.Event()
    startup = asyncio.create_task(ready.wait())
    job = AsyncMock()
    schedule = ElectricitySchedule(None)
    for name in (
        "_run_weekly_catalog_refresh",
        "_run_daily_reading_collection",
        "_retry_failed_daily_collection",
    ):
        monkeypatch.setattr(schedule, name, job)
    schedule.start(startup_task=startup)
    ready.set()
    await asyncio.gather(*schedule._tasks)
    assert job.await_count == 3


async def test_http_binding_verification_rejects_revoked_password(monkeypatch):
    monkeypatch.setenv("CRAWLER_INTERNAL_TOKEN", "test-token")
    monkeypatch.setattr(auth, "is_in_maintenance_window", lambda: False)
    cached = httpx.Cookies()
    cached.set("sid", "previously-valid-session")
    cache_read = AsyncMock(return_value=cached)
    fresh_login = AsyncMock(side_effect=ValueError("password revoked"))
    monkeypatch.setattr(auth, "get_cookies_from_redis", cache_read)
    monkeypatch.setattr(auth, "get_cookies_from_jwxt", fresh_login)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/academic",
            headers={"Authorization": "Bearer test-token"},
            json={
                "action": "get_profile",
                "username": "student",
                "password": "old",
                "verify_credentials": True,
            },
        )
    assert response.status_code == 200
    assert response.json()["error_code"] == "ACADEMIC_LOGIN_FAILED"
    cache_read.assert_not_awaited()
    fresh_login.assert_awaited_once()


@pytest.mark.parametrize("competing_writer", [False, True])
def test_bootstrap_failure_or_race_never_overwrites_pool(
    tmp_path, monkeypatch, competing_writer
):
    source, destination = tmp_path / "seed.json", tmp_path / "state" / "pool.json"
    source.write_text('{"accounts": [{"xh": "seed", "pwd": "seed-password"}]}')

    def publish(*args):
        if competing_writer:
            destination.write_text(
                '{"accounts": [{"xh": "api-owner", "pwd": "current"}]}'
            )
            raise FileExistsError()
        raise OSError("publish interrupted")

    monkeypatch.setattr(os, "link", publish)
    if competing_writer:
        assert not seed_account_pool(source, destination)
        assert json.loads(destination.read_text())["accounts"][0]["xh"] == "api-owner"
        assert list(destination.parent.iterdir()) == [destination]
    else:
        with pytest.raises(OSError, match="publish interrupted"):
            seed_account_pool(source, destination)
        assert not destination.exists()
        assert list(destination.parent.iterdir()) == []
