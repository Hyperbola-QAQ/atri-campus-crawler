import importlib.util
import json
from pathlib import Path

import pytest

from services.electricity import ElectricityQueryError


def _monitor_module():
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "detect_electricity_update.py"
    )
    spec = importlib.util.spec_from_file_location("electricity_update_monitor", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_monitor_records_an_unavailable_platform_and_keeps_running(tmp_path):
    monitor = _monitor_module()

    class UnavailableService:
        async def query_live(self, _room_number, _campus):
            raise ElectricityQueryError("电费平台暂不可用")

    class ServiceFactory:
        @classmethod
        def from_environment(cls):
            return UnavailableService()

    monitor.ElectricityService = ServiceFactory
    state_path = tmp_path / "monitor-state.json"

    await monitor.observe_once("hanpu", "06417", state_path)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["observations"] == []
    assert state["failures"][0]["error"] == "电费平台暂不可用"
