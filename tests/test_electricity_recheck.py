"""后台重采冷却60分钟，前台按条件直查，11点不再发起财务请求。"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

import main
from services import electricity, electricity_cache
from services.electricity import (
    ElectricityAccountPool,
    ElectricityQueryError,
    ElectricityService,
)
from services.electricity_cache import DailyElectricityCache


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex):
        self.values[key] = value


@pytest.fixture
def service(tmp_path, monkeypatch):
    state = {"now": datetime(2026, 10, 24, 1, tzinfo=timezone.utc)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"].astimezone(tz)

    monkeypatch.setattr(electricity, "datetime", Clock)
    monkeypatch.setattr(electricity_cache, "datetime", Clock)
    monkeypatch.setattr(main, "datetime", Clock)
    value = ElectricityService(
        "https://portal.test",
        ElectricityAccountPool(tmp_path / "accounts.json"),
        reading_cache=DailyElectricityCache(
            redis_client=MemoryRedis(), retention_days=2
        ),
        retry_cache=DailyElectricityCache(
            redis_client=MemoryRedis(), key_prefix="attempt"
        ),
    )
    value.catalog_path = None
    value.collection_state_path = None
    monkeypatch.setattr(
        value,
        "get_room_catalog",
        lambda: {
            "rooms": [
                {"campus": "area", "campus_name": "含浦学生宿舍", "room_number": r}
                for r in ["06627", "06628"]
            ]
        },
    )

    async def live(room, campus):
        return {
            "meter_number": "test-meter",
            "balance": 16.47,
            "campus": campus,
            "room_number": room,
            "collected_at": state["now"].isoformat(),
        }

    value.query_live = AsyncMock(side_effect=live)
    return value, state


async def seed_previous(value, state):
    now = state["now"]
    state["now"] -= timedelta(days=1)
    await seed(value, state)
    state["now"] = now


async def seed(value, state, room="06627"):
    await value.reading_cache.set(
        "area",
        room,
        {
            "meter_number": "test-meter",
            "balance": 22.44,
            "collected_at": (state["now"] - timedelta(minutes=61)).isoformat(),
        },
    )


async def test_targeted_recollection_bypasses_daily_cache_and_deduplicates(service):
    value, state = service
    await seed(value, state)
    await seed(value, state, "06628")
    result = await value.collect_room_readings(
        retry_rooms=[("hanpu", "06627"), ("area", "06627"), ("area", "06629")],
        interval_seconds=0.001,
    )
    assert result == {"succeeded": 1, "failed": 0}
    value.query_live.assert_awaited_once_with("06627", "area")
    assert (await value.reading_cache.get("area", "06627"))["balance"] == 16.47
    assert (await value.reading_cache.get("area", "06628"))["balance"] == 22.44
    assert await value.collect_room_readings(retry_rooms=[("area", "06627")]) == {
        "succeeded": 0,
        "failed": 0,
    }


async def test_failed_attempt_also_cools_down_for_60_minutes(service):
    value, state = service
    await seed(value, state)
    value.query_live.side_effect = ElectricityQueryError("offline")
    assert (await value.collect_room_readings(retry_rooms=[("area", "06627")]))[
        "failed"
    ] == 1
    state["now"] += timedelta(minutes=59)
    await value.collect_room_readings(retry_rooms=[("area", "06627")])
    assert value.query_live.await_count == 1
    state["now"] += timedelta(minutes=1)
    await value.collect_room_readings(retry_rooms=[("area", "06627")])
    assert value.query_live.await_count == 2
    assert (await value.reading_cache.get("area", "06627"))["balance"] == 22.44


async def test_deadline_is_checked_between_rooms(service):
    value, state = service
    state["now"] = state["now"].replace(hour=2, minute=59)
    await seed(value, state)
    await seed(value, state, "06628")

    async def cross_deadline(room, campus):
        state["now"] = state["now"].replace(hour=3, minute=0)
        return {
            "meter_number": "test-meter",
            "balance": 16.47,
            "collected_at": state["now"].isoformat(),
        }

    value.query_live.side_effect = cross_deadline
    result = await value.collect_room_readings(
        retry_rooms=[("area", "06627"), ("area", "06628")],
        batch_size=1,
        interval_seconds=0.001,
    )
    assert result == {"succeeded": 1, "failed": 0}
    assert value.query_live.await_count == 1
    await value.collect_room_readings(retry_rooms=[("area", "06628")])
    assert value.query_live.await_count == 1


async def test_retry_api_requires_internal_token_and_transmits_only_ids(
    service, monkeypatch
):
    value, state = service
    await seed(value, state)
    monkeypatch.setenv("CRAWLER_INTERNAL_TOKEN", "retry-test-token")

    async def dependency():
        return value

    main.app.dependency_overrides[main.get_electricity_service] = dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            body = {"rooms": [{"campus": "area", "room_number": "06627"}]}
            denied = await client.post("/api/v1/electricity/retry-unchanged", json=body)
            assert denied.status_code == 401
            value.query_live.assert_not_awaited()
            accepted = await client.post(
                "/api/v1/electricity/retry-unchanged",
                json=body,
                headers={"Authorization": "Bearer retry-test-token"},
            )
            assert accepted.status_code == 200
            assert accepted.json() == {"succeeded": 1, "failed": 0}
    finally:
        main.app.dependency_overrides.clear()


async def test_foreground_requeries_each_time_until_reading_changes(service):
    value, state = service
    await seed_previous(value, state)
    await value.reading_cache.set(
        "area",
        "06627",
        {
            "meter_number": "test-meter",
            "balance": 22.44,
            "collected_at": state["now"].isoformat(),
        },
    )
    balances = iter([22.44, 22.44, 16.47])

    async def live(room, campus):
        return {
            "meter_number": "test-meter",
            "balance": next(balances),
            "collected_at": state["now"].isoformat(),
        }

    value.query_live.side_effect = live
    for expected in (22.44, 22.44, 16.47, 16.47):
        assert (await value.get_query_room_reading("06627", "hanpu"))[1][
            "balance"
        ] == expected
    assert value.query_live.await_count == 3


async def test_server_condition_is_used_if_redis_yesterday_is_missing(service):
    value, state = service
    await seed(value, state)
    assert (await value.get_query_room_reading("06627", "area"))[1]["balance"] == 22.44
    value.query_live.assert_not_awaited()
    assert (await value.get_query_room_reading("06627", "area", known_unchanged=True))[
        1
    ]["balance"] == 16.47
    value.query_live.assert_awaited_once()


async def test_foreground_stops_at_11_even_if_unchanged(service):
    value, state = service
    await seed_previous(value, state)
    await seed(value, state)
    state["now"] = state["now"].replace(hour=3)
    assert (await value.get_query_room_reading("06627", "area", known_unchanged=True))[
        1
    ]["balance"] == 22.44
    value.query_live.assert_not_awaited()


async def test_foreground_live_failure_preserves_last_good_cache(service):
    value, state = service
    await seed_previous(value, state)
    await seed(value, state)
    value.query_live.side_effect = ElectricityQueryError("offline")
    with pytest.raises(ElectricityQueryError):
        await value.get_query_room_reading("06627", "area")
    assert (await value.reading_cache.get("area", "06627"))["balance"] == 22.44


async def test_query_api_protects_conditional_refresh_with_internal_token(
    service, monkeypatch
):
    value, state = service
    await seed_previous(value, state)
    await seed(value, state)
    monkeypatch.setenv("CRAWLER_INTERNAL_TOKEN", "query-test-token")

    async def dependency():
        return value

    main.app.dependency_overrides[main.get_electricity_service] = dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as client:
            path = "/api/v1/electricity/area/06627"
            for flag in ("true", "1", "yes", "on"):
                denied = await client.get(path, params={"recheck": flag})
                assert denied.status_code == 401
            value.query_live.assert_not_awaited()
            current = await client.get(
                path,
                params={"recheck": "true"},
                headers={"Authorization": "Bearer query-test-token"},
            )
            assert current.status_code == 200
            assert current.json()["balance"] == 16.47
    finally:
        main.app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "current,previous,expected",
    [
        (
            {
                "meter_number": "test-meter",
                "balance": "22.440",
                "remaining_electricity": "36.250 kWh",
            },
            {
                "meter_number": "test-meter",
                "balance": 22.44,
                "remaining_electricity": "36.25kWh",
            },
            True,
        ),
        (
            {"balance": 22.44, "meter_number": "a"},
            {"balance": 22.44, "meter_number": "b"},
            False,
        ),
        (
            {"meter_number": "test-meter", "balance": "NaN"},
            {"meter_number": "test-meter", "balance": "NaN"},
            False,
        ),
        (
            {"meter_number": "test-meter", "balance": "unknown"},
            {"meter_number": "test-meter", "balance": 22.44},
            False,
        ),
        ({"meter_number": "test-meter", "balance": 22.44}, {}, False),
        ({}, {}, False),
    ],
)
def test_invalid_or_different_meter_readings_do_not_trigger_refresh(
    current, previous, expected
):
    assert electricity.same_reading(current, previous) is expected


async def test_missing_catalog_never_triggers_finance_request(service, monkeypatch):
    value, _ = service
    monkeypatch.setattr(value, "get_room_catalog", lambda: None)
    assert await value.get_query_room_reading(
        "06627", "area", known_unchanged=True
    ) == (None, None)
    with pytest.raises(ElectricityQueryError, match="目录"):
        await value.collect_room_readings(retry_rooms=[("area", "06627")])
    value.query_live.assert_not_awaited()


@pytest.mark.parametrize(
    "collected_at",
    [None, "invalid", "2026-10-24T00:00:00", "2026-10-23T01:00:00+00:00"],
)
async def test_background_rejects_invalid_or_wrong_day_collection_time(
    service, collected_at
):
    value, _ = service
    await value.reading_cache.set(
        "area",
        "06627",
        {"meter_number": "test-meter", "balance": 22.44, "collected_at": collected_at},
    )
    assert await value.collect_room_readings(retry_rooms=[("area", "06627")]) == {
        "succeeded": 0,
        "failed": 0,
    }
    value.query_live.assert_not_awaited()


@pytest.mark.parametrize("meter", [None, "", "   ", 123])
def test_missing_or_invalid_meter_never_matches(meter):
    reading = {"meter_number": meter, "balance": 22.44}
    assert not electricity.same_reading(reading, reading)


def test_missing_meter_and_valid_meter_without_values_never_match():
    assert not electricity.same_reading({"balance": 22.44}, {"balance": 22.44})
    assert not electricity.same_reading(
        {"meter_number": "test-meter"}, {"meter_number": "test-meter"}
    )


@pytest.mark.parametrize(
    "rooms,hour",
    [([], 1), ([], 3), ([("area", "06627")], 3), ([("area", "06627")], 21)],
)
async def test_noop_retry_never_waits_for_busy_collection_lock(service, rooms, hour):
    value, state = service
    state["now"] = state["now"].replace(hour=hour)
    await value._collection_lock.acquire()
    try:
        result = await asyncio.wait_for(
            value.collect_room_readings(retry_rooms=rooms), timeout=0.1
        )
        assert result == {"succeeded": 0, "failed": 0}
        assert value._collection_lock.locked()
        value.query_live.assert_not_awaited()
    finally:
        value._collection_lock.release()


async def test_queued_retry_rechecks_deadline_after_collection_lock(service):
    value, state = service
    await seed(value, state)
    await value._collection_lock.acquire()
    task = asyncio.create_task(
        value.collect_room_readings(retry_rooms=[("area", "06627")])
    )
    try:
        await asyncio.sleep(0)
        assert not task.done()
        state["now"] = state["now"].replace(hour=3)
    finally:
        value._collection_lock.release()
    assert await asyncio.wait_for(task, timeout=1) == {"succeeded": 0, "failed": 0}
    value.query_live.assert_not_awaited()


async def test_collection_aggregates_only_fixed_final_failure_reasons(service, caplog):
    value, _ = service
    value.query_live.side_effect = [
        ElectricityQueryError("private-upstream-text", reason="meter_unavailable"),
        ElectricityQueryError("private-account-text", reason="untrusted-private-label"),
    ]
    assert await value.collect_room_readings(force=True, interval_seconds=0.001) == {
        "succeeded": 0,
        "failed": 2,
    }
    message = caplog.records[-1].getMessage()
    assert "reason_counts={'meter_unavailable': 1, 'unknown': 1}" in message
    assert "count_unit=catalog_row" in message
    assert "private" not in message
    assert "06627" not in message


async def test_account_pool_failures_are_separate_from_unknown(service, caplog):
    from services.electricity import AccountPoolConfigurationError

    value, _ = service
    value.query_live.side_effect = AccountPoolConfigurationError("private-value")
    assert (await value.collect_room_readings(force=True, interval_seconds=0.001))[
        "failed"
    ] == 2
    assert "reason_counts={'account_pool': 2}" in caplog.records[-1].getMessage()
    assert "private-value" not in caplog.text


@pytest.mark.parametrize(
    "reasons,expected",
    [
        (["http_request", "http_request"], "http_request"),
        (["http_request", "unknown"], "mixed"),
    ],
)
async def test_account_rotation_counts_one_final_reason_without_changing_message(
    service, reasons, expected
):
    from types import SimpleNamespace
    from adapter.hnucm_adapter.electricity import ElectricityPlatformError

    value, _ = service
    value.base_url = "https://portal.test"
    value.account_pool.rotated_accounts = AsyncMock(
        return_value=[
            SimpleNamespace(cache_key="one"),
            SimpleNamespace(cache_key="two"),
        ]
    )
    value.cookie_cache.delete = AsyncMock()
    value._query_account = AsyncMock(
        side_effect=[
            ElectricityPlatformError("same-safe-message", reason=reason)
            for reason in reasons
        ]
    )
    with pytest.raises(ElectricityQueryError, match="same-safe-message") as caught:
        await ElectricityService.query_live(value, "06627", "area")
    assert caught.value.reason == expected


@pytest.mark.parametrize(
    "payload,reason", [({}, "reading_unavailable"), ([], "upstream_response")]
)
def test_adapter_serialization_failures_keep_fixed_reason(payload, reason):
    from adapter.hnucm_adapter.electricity import (
        ElectricityPlatformError,
        HNUCMElectricityClient,
    )

    with pytest.raises(ElectricityPlatformError) as caught:
        HNUCMElectricityClient._serialize_result("06627", "area", payload)
    assert caught.value.reason == reason


async def test_adapter_empty_meter_selection_is_unavailable_not_proven_unbound():
    from adapter.hnucm_adapter.electricity import (
        ElectricityPlatformError,
        HNUCMElectricityClient,
    )

    client = HNUCMElectricityClient("https://portal.test")
    client._resolve_room_selections = AsyncMock(return_value=[])
    with pytest.raises(ElectricityPlatformError) as caught:
        await client._get_electricity(None, "unused", "06627", "area")
    assert caught.value.reason == "meter_unavailable"


async def test_crossing_deadline_reports_prior_failure_exactly_once(service, caplog):
    value, state = service
    await seed(value, state)
    await seed(value, state, "06628")

    async def fail_and_cross_deadline(room, campus):
        state["now"] = state["now"].replace(hour=3)
        raise ElectricityQueryError("private-response", reason="http_request")

    value.query_live.side_effect = fail_and_cross_deadline
    assert await value.collect_room_readings(
        retry_rooms=[("area", "06627"), ("area", "06628")],
        interval_seconds=0.001,
    ) == {"succeeded": 0, "failed": 1}
    value.query_live.assert_awaited_once()
    records = [
        r
        for r in caplog.records
        if "Daily electricity collection incomplete:" in r.getMessage()
    ]
    assert len(records) == 1
    assert "reason_counts={'http_request': 1}" in records[0].getMessage()
    assert "private-response" not in records[0].getMessage()
