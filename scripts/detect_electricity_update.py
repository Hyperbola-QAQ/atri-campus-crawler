"""观察测试寝室实时电量，检测平台刷新时间。

运行前设置 ELECTRICITY_UPDATE_MONITOR_CAMPUS 和 ELECTRICITY_UPDATE_MONITOR_ROOM_NUMBER。
默认持续运行，在每小时 :00 和 :30 采样；由外部 cron 每半小时调用时使用 --once。
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.electricity import (  # noqa: E402
    AccountPoolConfigurationError,
    ElectricityQueryError,
    ElectricityService,
)


def _timezone() -> ZoneInfo:
    """显式 IANA 时区，默认北京时间；未知时区直接失败，不回退宿主时间。"""
    return ZoneInfo(os.getenv("ELECTRICITY_SCHEDULE_TIMEZONE", "Asia/Shanghai"))


def _reading_fingerprint(reading: dict[str, Any]) -> str:
    """固定电表字段排序哈希，忽略 collected_at，避免采集时间或键序变化误报刷新。"""
    observed = {
        key: reading.get(key)
        for key in (
            "meter_number",
            "remaining_electricity",
            "balance",
            "state",
            "category",
        )
    }
    payload = json.dumps(observed, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_state(path: Path) -> dict[str, Any]:
    """缺失、不可读或非对象状态返回空历史，补齐缺失列表，不校验已有列表内容。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"observations": [], "updates": [], "failures": []}
    if not isinstance(data, dict):
        return {"observations": [], "updates": [], "failures": []}
    data.setdefault("observations", [])
    data.setdefault("updates", [])
    data.setdefault("failures", [])
    return data


def _write_state(path: Path, state: dict[str, Any]) -> None:
    """同目录临时 JSON 原子替换，供中断恢复，不注册系统调度。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_path, path)


def _suggest_cron(updates: list[dict[str, Any]]) -> str | None:
    """至少两个可解析变化时刻且分钟一致才建议 cron，同小时固定，否则每小时。
    只反映首次观察窗口，不证明实际刷新瞬间。
    """
    timestamps: list[datetime] = []
    for update in updates:
        try:
            timestamps.append(datetime.fromisoformat(update["observed_at"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(timestamps) < 2:
        return None
    minutes = {timestamp.minute for timestamp in timestamps}
    hours = {timestamp.hour for timestamp in timestamps}
    if len(minutes) != 1:
        return None
    minute = minutes.pop()
    return f"{minute} {'*' if len(hours) > 1 else hours.pop()} * * *"


async def observe_once(campus: str, room_number: str, state_path: Path) -> None:
    """绕过日缓存探测，首个成功值仅建立基准，已知查询失败保存失败记录以便后续继续。
    保留最多 1488 次观测及各 100 次更新、失败；cron 仅建议，不修改调度。
    """
    now = datetime.now(_timezone()).replace(second=0, microsecond=0)
    state = _read_state(state_path)
    try:
        reading = await ElectricityService.from_environment().query_live(
            room_number, campus
        )
    except (AccountPoolConfigurationError, ElectricityQueryError) as exc:
        state["failures"].append({"observed_at": now.isoformat(), "error": str(exc)})
        state["failures"] = state["failures"][-100:]
        _write_state(state_path, state)
        print(f"财务系统暂不可用：{now.isoformat()}，{exc}；将在下个半小时继续检测")
        return

    fingerprint = _reading_fingerprint(reading)
    observations = state["observations"]
    previous = next(
        (
            observation
            for observation in reversed(observations)
            if isinstance(observation, dict) and observation.get("fingerprint")
        ),
        None,
    )
    changed = previous is not None and previous.get("fingerprint") != fingerprint
    observation = {
        "observed_at": now.isoformat(),
        "fingerprint": fingerprint,
        "remaining_electricity": reading.get("remaining_electricity"),
        "balance": reading.get("balance"),
    }
    observations.append(observation)
    # 保留一个月的半小时探测历史，便于审计。
    state["observations"] = observations[-1488:]
    current_reading = (
        f"剩余电量={reading.get('remaining_electricity')!s}，"
        f"余额={reading.get('balance')!s}"
    )
    if changed:
        update = {**observation, "previous_observed_at": previous.get("observed_at")}
        state["updates"].append(update)
        state["updates"] = state["updates"][-100:]
        print(f"检测到电费更新：{now.isoformat()}，{current_reading}")
    else:
        print(f"电费未变化：{now.isoformat()}，{current_reading}")
    suggested_cron = _suggest_cron(state["updates"])
    state["suggested_financial_system_update_cron"] = suggested_cron
    _write_state(state_path, state)
    if suggested_cron:
        print(
            "建议硬编码到 services/electricity_schedule.py："
            f"FINANCIAL_SYSTEM_ELECTRICITY_SETTLEMENT_CRON = {suggested_cron!r}"
        )


def _next_half_hour(now: datetime) -> datetime:
    """取严格未来整点或半点，清秒和微秒，避免重复立即探测。"""
    candidate = now.replace(second=0, microsecond=0)
    if candidate.minute < 30:
        return candidate.replace(minute=30)
    return (candidate + timedelta(hours=1)).replace(minute=0)


async def main() -> None:
    """参数优先于环境，目标必须显式提供；--once 用于外部调度，否则进程内每半小时探测。
    等待至少一秒避免忙循环，不自行安装调度任务。
    """
    parser = argparse.ArgumentParser(description="每半小时探测测试寝室的电费刷新")
    parser.add_argument(
        "--once", action="store_true", help="只探测一次，供系统 cron 调用"
    )
    parser.add_argument(
        "--campus", default=os.getenv("ELECTRICITY_UPDATE_MONITOR_CAMPUS")
    )
    parser.add_argument(
        "--room-number", default=os.getenv("ELECTRICITY_UPDATE_MONITOR_ROOM_NUMBER")
    )
    parser.add_argument(
        "--state-file",
        default=os.getenv(
            "ELECTRICITY_UPDATE_MONITOR_STATE_FILE",
            "./config/electricity_update_monitor_state.json",
        ),
    )
    args = parser.parse_args()
    if not args.campus or not args.room_number:
        parser.error("请设置测试寝室的 campus 和 room number（参数或环境变量）")
    state_path = Path(args.state_file).expanduser()
    while True:
        await observe_once(args.campus, args.room_number.replace("-", ""), state_path)
        if args.once:
            return
        delay = (
            _next_half_hour(datetime.now(_timezone())) - datetime.now(_timezone())
        ).total_seconds()
        await asyncio.sleep(max(1, delay))


if __name__ == "__main__":
    asyncio.run(main())
