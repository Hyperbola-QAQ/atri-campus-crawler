"""围绕财务结算窗口调度采集，显式使用 IANA 时区，任务由 API 生命周期回收。"""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Awaitable, Callable

from services.electricity import ElectricityService

logger = logging.getLogger(__name__)

# 05:30是维护结束后的首轮采集起点，不保证上游此后不再更新。
# server在本地比较昨日值，仅对仍相同的寝室在11:00前申请有限重采。
FINANCIAL_SYSTEM_MAINTENANCE_START = time(2)
FINANCIAL_SYSTEM_MAINTENANCE_END = time(5, 30)
FINANCIAL_SYSTEM_DAILY_DATA_AVAILABLE_AT = FINANCIAL_SYSTEM_MAINTENANCE_END
FINANCIAL_SYSTEM_ELECTRICITY_SETTLEMENT_CRON = "30 5 * * *"

# 为日采集留出一个完整的半小时启动窗口；失败重试不应与首轮全量采集同时开始。
FIRST_DAILY_COLLECTION_RETRY_AT = time(6)
LAST_DAILY_COLLECTION_RETRY_AT = time(23)


class ElectricitySchedule:
    """进程内目录刷新、采集及补采调度器，不提供跨进程选主。"""

    def __init__(self, service: ElectricityService):
        """保存服务与时区，不创建任务；非法时区直接失败。"""
        self.service = service
        self.financial_system_electricity_settlement_cron = (
            FINANCIAL_SYSTEM_ELECTRICITY_SETTLEMENT_CRON
        )
        self.timezone = ZoneInfo(
            os.getenv("ELECTRICITY_SCHEDULE_TIMEZONE", "Asia/Shanghai")
        )
        self._tasks: list[asyncio.Task[None]] = []

    def start(self, *, startup_task: asyncio.Task | None = None) -> None:
        """启动三个循环，先等待 startup_task；调用方须保证只启动一次。"""
        self._tasks = [
            asyncio.create_task(self._after_startup(startup_task, job))
            for job in (
                self._run_weekly_catalog_refresh,
                self._run_daily_reading_collection,
                self._retry_failed_daily_collection,
            )
        ]

    async def _after_startup(
        self,
        startup_task: asyncio.Task | None,
        job: Callable[[], Awaitable[None]],
    ) -> None:
        """启动恢复失败仍进入调度；shield 防止本任务取消连带取消共享恢复任务。
        恢复任务须由生命周期持有者另行取消并等待。
        """
        if startup_task is not None:
            try:
                await asyncio.shield(startup_task)
            except Exception:
                logger.exception(
                    "Electricity startup failed; scheduled jobs will retry"
                )
        await job()

    async def stop(self) -> None:
        """先全部取消再等待，避免等待期间继续触发采集；非取消异常向上传播。"""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run_weekly_catalog_refresh(self) -> None:
        """每周一 11:30 刷新目录，失败后仍等待下一周，不触发读数采集。"""
        while True:
            await self._sleep_until(self._next_weekday(time(11, 30), weekday=0))
            try:
                await self.service.refresh_room_catalog()
            except Exception:
                logger.exception("Scheduled electricity room catalog refresh failed")

    async def _resume_interrupted_collection(self) -> None:
        """仅结算后恢复当日断点，失败只记录，不阻止后续计划任务。"""
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
        """下一次 05:30 强制全量采集，单轮异常不终止调度。"""
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
        """半小时窗口仅补采缺失读数，已完成时不访问平台，失败留待下一窗口。"""
        while True:
            await self._sleep_until(self._next_collection_retry())
            try:
                if await self.service.needs_today_collection():
                    result = await self.service.collect_room_readings(
                        retry_missing=True
                    )
                    logger.info("Retried electricity reading collection: %s", result)
            except Exception as exc:
                logger.warning("Electricity reading collection will retry: %s", exc)

    async def _sleep_until(self, scheduled_at: datetime) -> None:
        """等待带时区计划时刻，过去时间按零秒处理。"""
        seconds = max(0, (scheduled_at - datetime.now(self.timezone)).total_seconds())
        await asyncio.sleep(seconds)

    def _next_daily(self, scheduled_time: time) -> datetime:
        """返回严格未来的每日时刻；恰好命中时推迟一天，避免重复触发。"""
        now = datetime.now(self.timezone)
        candidate = datetime.combine(now.date(), scheduled_time, tzinfo=self.timezone)
        return candidate if candidate > now else candidate + timedelta(days=1)

    def _next_weekday(self, scheduled_time: time, *, weekday: int) -> datetime:
        """按 Python 周一为零返回严格未来窗口，已到本周时刻则推迟七天。"""
        now = datetime.now(self.timezone)
        days = (weekday - now.weekday()) % 7
        candidate = datetime.combine(
            now.date() + timedelta(days=days), scheduled_time, tzinfo=self.timezone
        )
        return candidate if candidate > now else candidate + timedelta(days=7)

    def _next_collection_retry(self) -> datetime:
        """返回 06:00（含）至 23:00（不含）的下一个整点或半点。
        窗口外及下一候选触及 23:00 时，等待最近的 06:00。
        """
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
