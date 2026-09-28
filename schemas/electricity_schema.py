from typing import Any

from pydantic import BaseModel


class ElectricityResponse(BaseModel):
    room_number: str
    name: str | None = None
    meter_number: str | None = None
    remaining_electricity: str | None = None
    balance: Any = None
    state: str | None = None
    category: str | None = None
