"""用模拟时钟覆盖调度边界和异常后的下一轮重试。"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import services.electricity_schedule as module


@pytest.mark.parametrize(
    "hour, minute, expected",
    [
        (5, 59, (6, 0, 5)),
        (6, 0, (6, 30, 5)),
        (6, 30, (7, 0, 5)),
        (22, 30, (6, 0, 6)),
        (23, 0, (6, 0, 6)),
    ],
)
def test_retry_boundary_clock(monkeypatch, hour, minute, expected):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))

    monkeypatch.setattr(module, "datetime", Clock)
    scheduled = module.ElectricitySchedule(None)._next_collection_retry()
    assert (scheduled.hour, scheduled.minute, scheduled.day) == expected


async def test_retry_survives_failure_and_skips_completed(monkeypatch):
    service = SimpleNamespace(
        needs_today_collection=AsyncMock(
            side_effect=[RuntimeError("offline"), False, True]
        ),
        collect_room_readings=AsyncMock(return_value={"completed": True}),
    )
    schedule = module.ElectricitySchedule(service)
    sleep = AsyncMock(side_effect=[None, None, None, asyncio.CancelledError()])
    monkeypatch.setattr(schedule, "_sleep_until", sleep)
    with pytest.raises(asyncio.CancelledError):
        await schedule._retry_failed_daily_collection()
    assert service.needs_today_collection.await_count == 3
    service.collect_room_readings.assert_awaited_once_with(retry_missing=True)


@pytest.mark.parametrize(
    "method, action",
    [
        ("_run_weekly_catalog_refresh", "refresh_room_catalog"),
        ("_run_daily_reading_collection", "collect_room_readings"),
    ],
)
async def test_job_loop_survives_error_and_propagates_cancel(
    monkeypatch, method, action
):
    call = AsyncMock(side_effect=[RuntimeError("offline"), {}])
    schedule = module.ElectricitySchedule(SimpleNamespace(**{action: call}))
    monkeypatch.setattr(
        schedule,
        "_sleep_until",
        AsyncMock(side_effect=[None, None, asyncio.CancelledError()]),
    )
    with pytest.raises(asyncio.CancelledError):
        await getattr(schedule, method)()
    assert call.await_count == 2
