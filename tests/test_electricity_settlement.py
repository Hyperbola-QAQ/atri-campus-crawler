from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import main


@pytest.mark.parametrize(
    "collected",
    [
        "2026-10-04T16:35:59+00:00",
        "2026-10-04T01:00:00+00:00",
        "2026-10-05T02:00:00+00:00",
        "2026-10-05T01:00:00",
        "invalid",
    ],
)
async def test_daily_api_rejects_unsettled_old_future_and_invalid_readings(
    monkeypatch, collected
):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(main, "datetime", Clock)
    service = SimpleNamespace(
        get_cached_room_reading=AsyncMock(
            return_value=(
                True,
                {
                    "campus": "hanpu",
                    "room_number": "06417",
                    "collected_at": collected,
                },
            )
        )
    )
    with pytest.raises(HTTPException) as error:
        await main.get_electricity("hanpu", "06417", service, "HNUCM")
    assert error.value.status_code == 503
    assert error.value.detail == "该寝室今日电费尚未采集完成"


async def test_daily_api_preserves_actual_settled_collection_time(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(main, "datetime", Clock)
    collected = "2026-10-04T21:30:00+00:00"
    service = SimpleNamespace(
        get_cached_room_reading=AsyncMock(
            return_value=(
                True,
                {
                    "campus": "hanpu",
                    "room_number": "06417",
                    "collected_at": collected,
                },
            )
        )
    )
    result = await main.get_electricity("hanpu", "06417", service, "HNUCM")
    assert result.collected_at == datetime.fromisoformat(collected)
