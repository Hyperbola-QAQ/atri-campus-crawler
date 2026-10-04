"""In-process schedules for refreshing the dorm catalog and daily readings."""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from services.electricity import ElectricityService

logger = logging.getLogger(__name__)

# 财务系统每日 02:00--05:30 维护。05:30 之后读取到的是当日结算后的数据，
# 因而采集与补采均以这个时刻为准，而不是沿用早期观察时的 11:00。
FINANCIAL_SYSTEM_MAINTENANCE_START = time(2)
FINANCIAL_SYSTEM_MAINTENANCE_END = time(5, 30)
FINANCIAL_SYSTEM_DAILY_DATA_AVAILABLE_AT = FINANCIAL_SYSTEM_MAINTENANCE_END
FINANCIAL_SYSTEM_ELECTRICITY_SETTLEMENT_CRON = "30 5 * * *"

# 为日采集留出一个完整的半小时启动窗口；失败重试不应与首轮全量采集同时开始。
FIRST_DAILY_COLLECTION_RETRY_AT = time(6)
LAST_DAILY_COLLECTION_RETRY_AT = time(23)


class ElectricitySchedule:
    """Run the two requested electricity jobs while the API process is alive."""

    def __init__(self, service: ElectricityService):
        self.service = service
        self.financial_system_electricity_settlement_cron = (
            FINANCIAL_SYSTEM_ELECTRICITY_SETTLEMENT_CRON
        )
        self.timezone = ZoneInfo(
            os.getenv("ELECTRICITY_SCHEDULE_TIMEZONE", "Asia/Shanghai")
        )
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._resume_interrupted_collection()),
            asyncio.create_task(self._run_weekly_catalog_refresh()),
            asyncio.create_task(self._run_daily_reading_collection()),
            asyncio.create_task(self._retry_failed_daily_collection()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run_weekly_catalog_refresh(self) -> None:
        while True:
            await self._sleep_until(self._next_weekday(time(11, 30), weekday=0))
            try:
                await self.service.refresh_room_catalog()
            except Exception:
                logger.exception("Scheduled electricity room catalog refresh failed")

    async def _resume_interrupted_collection(self) -> None:
        if (
            datetime.now(self.timezone).time()
            < FINANCIAL_SYSTEM_DAILY_DATA_AVAILABLE_AT
        ):
            return
        try:
            result = await self.service.resume_today_collection()
            if result is not None:
                logger.info("Resumed daily electricity collection: %s", result)
        except Exception:
            logger.exception("Interrupted electricity collection resume failed")

    async def _run_daily_reading_collection(self) -> None:
        while True:
            await self._sleep_until(
                self._next_daily(FINANCIAL_SYSTEM_DAILY_DATA_AVAILABLE_AT)
            )
            try:
                result = await self.service.collect_room_readings(force=True)
                logger.info("Daily electricity collection finished: %s", result)
            except Exception:
                logger.exception("Scheduled electricity reading collection failed")

    async def _retry_failed_daily_collection(self) -> None:
        """Retry unavailable readings after the 05:30 maintenance window."""
        while True:
            await self._sleep_until(self._next_collection_retry())
            try:
                if await self.service.needs_today_collection():
                    result = await self.service.collect_room_readings(
                        retry_missing=True
                    )
                    logger.info("Retried electricity reading collection: %s", result)
            except Exception as exc:
                # Keep this task alive so that it can retry when the platform
                # becomes available again.
                logger.warning("Electricity reading collection will retry: %s", exc)

    async def _sleep_until(self, scheduled_at: datetime) -> None:
        seconds = max(0, (scheduled_at - datetime.now(self.timezone)).total_seconds())
        await asyncio.sleep(seconds)

    def _next_daily(self, scheduled_time: time) -> datetime:
        now = datetime.now(self.timezone)
        candidate = datetime.combine(now.date(), scheduled_time, tzinfo=self.timezone)
        return candidate if candidate > now else candidate + timedelta(days=1)

    def _next_weekday(self, scheduled_time: time, *, weekday: int) -> datetime:
        now = datetime.now(self.timezone)
        days = (weekday - now.weekday()) % 7
        candidate = datetime.combine(
            now.date() + timedelta(days=days), scheduled_time, tzinfo=self.timezone
        )
        return candidate if candidate > now else candidate + timedelta(days=7)

    def _next_collection_retry(self) -> datetime:
        now = datetime.now(self.timezone)
        first_retry = datetime.combine(
            now.date(), FIRST_DAILY_COLLECTION_RETRY_AT, tzinfo=self.timezone
        )
        last_retry = datetime.combine(
            now.date(), LAST_DAILY_COLLECTION_RETRY_AT, tzinfo=self.timezone
        )
        if now < first_retry:
            return first_retry
        if now >= last_retry:
            return first_retry + timedelta(days=1)
        candidate = now.replace(second=0, microsecond=0)
        if candidate.minute < 30:
            candidate = candidate.replace(minute=30)
        else:
            candidate = (candidate + timedelta(hours=1)).replace(minute=0)
        return candidate if candidate < last_retry else first_retry + timedelta(days=1)
