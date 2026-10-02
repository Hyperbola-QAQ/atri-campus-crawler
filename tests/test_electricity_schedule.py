from datetime import datetime, time

import pytest
from zoneinfo import ZoneInfo

from services.electricity_schedule import (
    FINANCIAL_SYSTEM_DAILY_DATA_AVAILABLE_AT,
    FINANCIAL_SYSTEM_ELECTRICITY_SETTLEMENT_CRON,
    FINANCIAL_SYSTEM_MAINTENANCE_END,
    FINANCIAL_SYSTEM_MAINTENANCE_START,
    FIRST_DAILY_COLLECTION_RETRY_AT,
    ElectricitySchedule,
)


@pytest.mark.asyncio
async def test_schedule_starts_and_stops_all_background_jobs():
    class Service:
        async def resume_today_collection(self):
            return None

    schedule = ElectricitySchedule(Service())
    schedule.start()

    assert len(schedule._tasks) == 4
    await schedule.stop()
    assert all(task.done() for task in schedule._tasks)


def test_schedule_calculates_future_daily_and_weekly_runs():
    class Service:
        pass

    schedule = ElectricitySchedule(Service())
    now = datetime.now(ZoneInfo("Asia/Shanghai"))

    daily = schedule._next_daily(FINANCIAL_SYSTEM_DAILY_DATA_AVAILABLE_AT)
    weekly = schedule._next_weekday(time(11, 30), weekday=0)

    assert daily > now
    assert weekly > now
    assert weekly.weekday() == 0
    assert weekly.time() == time(11, 30)


def test_financial_maintenance_window_and_daily_data_publication_time_are_fixed():
    assert FINANCIAL_SYSTEM_MAINTENANCE_START == time(2)
    assert FINANCIAL_SYSTEM_MAINTENANCE_END == time(5, 30)
    assert FINANCIAL_SYSTEM_DAILY_DATA_AVAILABLE_AT == time(5, 30)
    assert FINANCIAL_SYSTEM_ELECTRICITY_SETTLEMENT_CRON == "30 5 * * *"


def test_schedule_retries_collections_only_during_the_daytime_window():
    class Service:
        pass

    schedule = ElectricitySchedule(Service())
    retry = schedule._next_collection_retry()

    assert retry > datetime.now(ZoneInfo("Asia/Shanghai"))
    assert FIRST_DAILY_COLLECTION_RETRY_AT <= retry.time() < time(23)
