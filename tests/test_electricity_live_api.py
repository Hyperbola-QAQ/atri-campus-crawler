"""Opt-in integration test against the real electricity platform."""

import pytest

from services.electricity import ElectricityService


@pytest.mark.electricity_live
@pytest.mark.asyncio
async def test_electricity_api_returns_live_meter_reading():
    service = ElectricityService.from_environment()
    catalog = service.get_room_catalog()
    if catalog is None:
        pytest.fail("run scripts/sync_electricity_rooms.py before the live test")
    room = next(
        (
            item
            for item in catalog["rooms"]
            if isinstance(item.get("campus"), str)
            and isinstance(item.get("room_number"), str)
        ),
        None,
    )
    if room is None:
        pytest.fail("the room catalog has no queryable dorm room")

    data = await service.query(room["room_number"], room["campus"])
    assert data["room_number"] == room["room_number"]
    assert data["campus"] == room["campus"]
    assert isinstance(data["remaining_electricity"], str)
    assert data["remaining_electricity"].strip()
