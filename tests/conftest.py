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
