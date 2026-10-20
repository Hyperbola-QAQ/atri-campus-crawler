"""默认离线，Redis 快速失败替身截断网络。
外部集成和 live 标记须显式开关，学校集成另需独立测试凭据。
"""

import os

import pytest


def pytest_collection_modifyitems(items):
    """根据标记与显式环境条件决定是否跳过外部依赖测试。

    三个开关互相独立，不能因为开启学校测试就自动访问真实电量平台。
    教务集成同时要求用户名与密码存在，缺一仍跳过。
    只增加跳过标记，不执行平台登录或输出环境凭据。
    """
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
    """让普通测试中的 Redis get、set 和 setex 立即产生连接错误。

    真实外部测试保留原连接能力，避免全局替身让集成测试假通过。
    先清空共享客户端引用，防止上个用例残留的事件循环或配置泄漏。
    此夹具不会启动 Redis 或修改用户系统服务。
    """
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
