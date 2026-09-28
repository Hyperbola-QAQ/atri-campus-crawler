"""End-to-end HTTP test against a running API and the real electricity portal."""

import os
import re

import httpx
import pytest


@pytest.mark.electricity_live
@pytest.mark.asyncio
async def test_electricity_api_returns_live_meter_reading():
    base_url = os.getenv("ELECTRICITY_API_TEST_BASE_URL", "").strip()
    room_number = os.getenv("ELECTRICITY_TEST_ROOM_NUMBER", "").strip()
    if not base_url:
        pytest.fail("set ELECTRICITY_API_TEST_BASE_URL to the running API URL")
    if not re.fullmatch(r"\d{4,5}", room_number):
        pytest.fail("set ELECTRICITY_TEST_ROOM_NUMBER to a 4-5 digit platform ROOMID")

    async with httpx.AsyncClient(
        base_url=base_url, timeout=90.0, trust_env=False
    ) as client:
        response = await client.get(f"/api/electricity/{room_number}")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["room_number"] == room_number.zfill(5)
    assert isinstance(data["remaining_electricity"], str)
    assert data["remaining_electricity"].strip()
