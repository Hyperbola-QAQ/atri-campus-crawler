"""账号文件损坏、目录持久化及断点采集的边界测试。"""

import json
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from adapter.hnucm_adapter.electricity import ElectricityPlatformError
from services import electricity as module
from services import electricity_schedule as schedule_module
from services.cookie_cache import PortalSession
from services.electricity import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    AccountPoolConfigurationError,
    ElectricityAccountPool,
    ElectricityService,
    ElectricityQueryError,
)
from services.electricity_cache import DailyElectricityCache


@pytest.fixture
def service(tmp_path):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps({"accounts": [{"xh": "student", "pwd": "password"}]})
    )
    instance = ElectricityService(
        "https://example.test", ElectricityAccountPool(accounts)
    )
    instance.catalog_path = tmp_path / "rooms.json"
    instance.collection_state_path = tmp_path / "state.json"
    return instance


@pytest.mark.parametrize("school", ["bad", "", "A-B", "A" * 33])
def test_invalid_pool_school(tmp_path, school):
    with pytest.raises(ValueError, match="学校代码"):
        ElectricityAccountPool(tmp_path / "accounts.json", school=school)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"accounts": None},
        {"accounts": [None]},
        {"accounts": [{"xh": 1, "pwd": "secret"}]},
        {"accounts": [{"xh": "s", "pwd": 1}]},
        {"accounts": [{"xh": " ", "pwd": "secret"}]},
        {"accounts": [{"xh": "s", "pwd": ""}]},
        {"accounts": [{"xh": "s", "pwd": "secret", "school": 1}]},
        {"accounts": [{"xh": "s", "pwd": "secret", "school": "bad"}]},
    ],
)
def test_invalid_account_document(tmp_path, data):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(data))
    with pytest.raises(AccountPoolConfigurationError):
        ElectricityAccountPool(path)._read_records()


@pytest.mark.parametrize("content", [b"not json", b"\xff"])
def test_unreadable_account_document(tmp_path, content):
    path = tmp_path / "accounts.json"
    path.write_bytes(content)
    with pytest.raises(AccountPoolConfigurationError, match="无法读取"):
        ElectricityAccountPool(path)._read_records()


async def test_account_crud_failures(service):
    pool = service.account_pool
    with pytest.raises(AccountAlreadyExistsError):
        await pool.add_account("student", "new")
    with pytest.raises(AccountNotFoundError):
        await pool.update_account("missing", new_username=None, password=None)
    with pytest.raises(AccountNotFoundError):
        await pool.delete_account("missing")
    await pool.add_account("other", "other-password")
    with pytest.raises(AccountAlreadyExistsError):
        await pool.update_account("student", new_username="other", password=None)
    assert (
        await pool.update_account("student", new_username="renamed", password=None)
        == "renamed"
    )
    assert (await pool.rotated_accounts())[0].password == "password"


def test_account_write_error(service, monkeypatch):
    monkeypatch.setattr(
        module.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("readonly"))
    )
    with pytest.raises(AccountPoolConfigurationError, match="无法写入"):
        service.account_pool._write_records([])


@pytest.mark.parametrize("method", ["query_live", "refresh_room_catalog"])
async def test_service_missing_url(service, method):
    service.base_url = ""
    with pytest.raises(AccountPoolConfigurationError, match="地址"):
        await (
            service.query_live("06417", "hanpu")
            if method == "query_live"
            else service.refresh_room_catalog()
        )


@pytest.mark.parametrize(
    "error",
    [
        None,
        RuntimeError("internal details"),
        ElectricityPlatformError("platform unavailable"),
    ],
)
async def test_catalog_discovery_result(service, error):
    client = SimpleNamespace(
        discover_rooms=AsyncMock(
            return_value=(
                [{"campus": "a", "room_number": "06417"}],
                PortalSession({"sid": "ok"}, "csrf"),
            ),
            side_effect=error,
        )
    )
    service.client_factory = lambda url: client
    if error:
        with pytest.raises(ElectricityQueryError) as caught:
            await service.refresh_room_catalog()
        assert "internal details" not in str(caught.value)
    else:
        catalog = await service.refresh_room_catalog()
        assert catalog["rooms"] == [{"campus": "a", "room_number": "06417"}]
        assert json.loads(service.catalog_path.read_text()) == catalog
        account = (await service.account_pool.rotated_accounts())[0]
        assert (await service.cookie_cache.get(account.cache_key)).cookies == {
            "sid": "ok"
        }


@pytest.mark.parametrize("kind", ["catalog", "state"])
@pytest.mark.parametrize("content", ["bad", "[]", '{"school":"OTHER"}'])
def test_corrupt_catalog_and_state(service, kind, content):
    path = service.catalog_path if kind == "catalog" else service.collection_state_path
    path.write_text(content)
    assert (
        service.get_room_catalog()
        if kind == "catalog"
        else service._read_collection_state()
    ) is None


@pytest.mark.parametrize("kind", ["catalog", "state"])
def test_optional_persistence_and_io_errors(service, monkeypatch, kind):
    attr = "catalog_path" if kind == "catalog" else "collection_state_path"
    write = (
        service._write_room_catalog
        if kind == "catalog"
        else service._write_collection_state
    )
    read = (
        service.get_room_catalog
        if kind == "catalog"
        else service._read_collection_state
    )
    setattr(service, attr, None)
    write({})
    assert read() is None
    setattr(service, attr, service.account_pool.path.parent / "optional.json")
    monkeypatch.setattr(
        module.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("readonly"))
    )
    write({})
    assert read() is None


async def test_uncatalogued_reading(service):
    assert await service.get_cached_room_reading("06417", "hanpu") == (None, None)


@pytest.mark.parametrize("total,queried", [("bad", -1), (-1, "bad"), (2, 8)])
def test_status_bounds(service, total, queried):
    service._write_collection_state(
        {"date": service._collection_date(), "total": total, "next_index": queried}
    )
    result = service.daily_collection_status()
    assert result["total"] == (2 if total == 2 else 0)
    assert result["queried"] == result["total"]


def test_status_missing_or_previous_day(service):
    assert service.daily_collection_status()["completed"] is False
    service._write_collection_state({"date": "2000-01-01", "next_index": 2, "total": 2})
    assert service.daily_collection_status()["total"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batch_size": 0},
        {"interval_seconds": 0},
        {"jitter_seconds": -1},
        {"force": True, "retry_missing": True},
    ],
)
async def test_invalid_collection_plan(service, kwargs):
    with pytest.raises(ValueError):
        await service.collect_room_readings(**kwargs)


async def test_collection_refreshes_missing_catalog_and_records_failed_rooms(
    service, monkeypatch
):
    rooms = [
        {"campus": "a", "room_number": "06417"},
        {"campus": "a", "room_number": "06418"},
        None,
        {"campus": "a"},
    ]
    refresh = AsyncMock(return_value={"rooms": rooms})
    monkeypatch.setattr(service, "refresh_room_catalog", refresh)
    monkeypatch.setattr(
        service, "query", AsyncMock(side_effect=[ElectricityQueryError("offline"), {}])
    )
    assert await service.collect_room_readings(
        batch_size=2, interval_seconds=0.001
    ) == {"succeeded": 1, "failed": 1}
    refresh.assert_awaited_once()
    assert service._read_collection_state()["next_index"] == 2


@pytest.mark.parametrize(
    "mode", ["no_state", "old", "no_catalog", "bad_index", "complete"]
)
async def test_resume_no_work(service, mode):
    if mode != "no_state":
        service._write_collection_state(
            {
                "date": "2000-01-01" if mode == "old" else service._collection_date(),
                "next_index": "bad" if mode == "bad_index" else 1,
            }
        )
    if mode != "no_catalog":
        service._write_room_catalog({"rooms": []})
    assert await service.resume_today_collection() is None


async def test_needs_collection_before_settlement_or_missing_catalog(
    service, monkeypatch
):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 7, 5, 29, tzinfo=tz)

    monkeypatch.setattr(module, "datetime", Clock)
    assert await service.needs_today_collection() is False
    assert await service.needs_today_collection(scheduled_time=time(0)) is True


@pytest.mark.parametrize("value", [123, "bad", "99999"])
async def test_cache_invalid_room_identity(value):
    cache = DailyElectricityCache(
        redis_client=SimpleNamespace(
            get=AsyncMock(return_value=json.dumps({"room_number": value}))
        )
    )
    assert await cache.get("a", "06417") is None
    with pytest.raises(ValueError, match="身份不一致"):
        await cache.set("a", "06417", {"room_number": value})


def test_cache_invalid_timezone():
    with pytest.raises(ValueError, match="IANA"):
        DailyElectricityCache(timezone_name="Invalid/Timezone")


async def test_memory_cache_expiry_and_redis_set_failure(monkeypatch):
    from redis.exceptions import ConnectionError

    redis = SimpleNamespace(
        set=AsyncMock(side_effect=ConnectionError("offline")),
        get=AsyncMock(return_value=None),
    )
    cache = DailyElectricityCache(redis_client=redis)
    await cache.set("a", "06417", {"balance": 1})
    redis.set.assert_awaited_once()
    assert await cache.get("a", "06417") == {"balance": 1}
    key = cache._key("a", "06417", cache._day_and_expiry()[0])
    cache._memory_entries[key] = (
        datetime.now(cache._timezone) - timedelta(seconds=1),
        {"balance": 1},
    )
    assert await cache.get("a", "06417") is None
    assert key not in cache._memory_entries


@pytest.mark.parametrize("mode", ["before", "empty", "success", "error"])
async def test_schedule_resume(monkeypatch, mode):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 7, 5 if mode == "before" else 6, 0, tzinfo=tz)

    monkeypatch.setattr(schedule_module, "datetime", Clock)
    resume = AsyncMock(
        return_value=None if mode == "empty" else {"succeeded": 1},
        side_effect=RuntimeError("offline") if mode == "error" else None,
    )
    schedule = schedule_module.ElectricitySchedule(
        SimpleNamespace(resume_today_collection=resume)
    )
    await schedule._resume_interrupted_collection()
    assert resume.await_count == (0 if mode == "before" else 1)


async def test_schedule_sleep_clamps_past_time(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(schedule_module.asyncio, "sleep", sleep)
    schedule = schedule_module.ElectricitySchedule(None)
    await schedule._sleep_until(
        datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(seconds=1)
    )
    sleep.assert_awaited_once_with(0)
