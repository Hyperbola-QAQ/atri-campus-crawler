"""In-process schedules for refreshing the dorm catalog and daily readings."""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from services.electricity import ElectricityService

logger = logging.getLogger(__name__)

# 运行 scripts/detect_electricity_update.py 得到稳定结论后，在此硬编码财务
# 系统结算电费的五段 cron，例如 "0 7 * * *" 或 "30 * * * *"。这是上游财务
# 系统的规律，不是本服务的采集任务，因而不通过接口或环境变量对外配置。
FINANCIAL_SYSTEM_ELECTRICITY_SETTLEMENT_CRON: str | None = None


class ElectricitySchedule:
    """Run the two requested electricity jobs while the API process is alive."""

    def __init__(self, service: ElectricityService):
        self.service = service
        self.financial_system_electricity_settlement_cron = (
            FINANCIAL_SYSTEM_ELECTRICITY_SETTLEMENT_CRON
        )
        self.timezone = ZoneInfo(os.getenv("ELECTRICITY_SCHEDULE_TIMEZONE", "Asia/Shanghai"))
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._resume_interrupted_collection()),
            asyncio.create_task(self._run_weekly_catalog_refresh()),
            asyncio.create_task(self._run_daily_reading_collection()),
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
        try:
            result = await self.service.resume_today_collection()
            if result is not None:
                logger.info("Resumed daily electricity collection: %s", result)
        except Exception:
            logger.exception("Interrupted electricity collection resume failed")

    async def _run_daily_reading_collection(self) -> None:
        while True:
            await self._sleep_until(self._next_daily(time(7, 30)))
            try:
                result = await self.service.collect_room_readings()
                logger.info("Daily electricity collection finished: %s", result)
            except Exception:
                logger.exception("Scheduled electricity reading collection failed")

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
