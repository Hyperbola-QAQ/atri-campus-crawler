import os

import pytest


def pytest_collection_modifyitems(items):
    """Keep tests that use live external services opt-in."""
    hnucm_enabled = os.getenv("RUN_HNUCM_INTEGRATION_TESTS") == "1"
    infrastructure_enabled = os.getenv("RUN_INFRA_INTEGRATION_TESTS") == "1"
    electricity_live_enabled = os.getenv("RUN_ELECTRICITY_LIVE_TESTS") == "1"
    has_credentials = bool(
        os.getenv("HNUCM_ADAPTER_TEST_USERNAME")
        and os.getenv("HNUCM_ADAPTER_TEST_PASSWORD")
    )

    for item in items:
        if item.get_closest_marker("integration") and not hnucm_enabled:
            item.add_marker(
                pytest.mark.skip(
                    reason="set RUN_HNUCM_INTEGRATION_TESTS=1 to run live university tests"
                )
            )
        elif item.get_closest_marker("integration") and not has_credentials:
            item.add_marker(
                pytest.mark.skip(
                    reason="provide HNUCM integration credentials through environment variables"
                )
            )
        if item.get_closest_marker("infrastructure") and not infrastructure_enabled:
            item.add_marker(
                pytest.mark.skip(
                    reason="set RUN_INFRA_INTEGRATION_TESTS=1 to use local Redis"
                )
            )
        if item.get_closest_marker("electricity_live") and not electricity_live_enabled:
            item.add_marker(
                pytest.mark.skip(
                    reason="set RUN_ELECTRICITY_LIVE_TESTS=1 to query the real electricity API"
                )
            )


@pytest.fixture(autouse=True)
def isolate_redis_for_offline_tests(monkeypatch, request):
    """普通测试立即走缓存降级；真实 Redis 只由 infrastructure 测试访问。"""
    if (
        request.node.get_closest_marker("infrastructure")
        or request.node.get_closest_marker("integration")
        or request.node.get_closest_marker("electricity_live")
    ):
        return
    from unittest.mock import AsyncMock
    from redis.asyncio import Redis
    from redis.exceptions import ConnectionError
    from utils import redis as redis_module

    monkeypatch.setattr(redis_module, "_REDIS_CLIENT", None)
    for method in ("get", "set", "setex"):
        monkeypatch.setattr(
            Redis, method, AsyncMock(side_effect=ConnectionError("offline test"))
        )
