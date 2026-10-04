from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from schemas.school import School


class ElectricityResponse(BaseModel):
    school: School = "HNUCM"
    campus: str
    room_number: str
    name: str | None = None
    meter_number: str | None = None
    remaining_electricity: str | None = None
    balance: Any = None
    collected_at: datetime | None = None
    state: str | None = None
    category: str | None = None


class ElectricityCollectionStatusResponse(BaseModel):
    """Progress of crawler's current daily all-room electricity collection."""

    school: School = "HNUCM"
    collection_date: str
    total: int = Field(ge=0)
    queried: int = Field(ge=0)
    completed: bool


class ElectricityAccountResponse(BaseModel):
    """Public account representation. Passwords are intentionally omitted."""

    school: School = "HNUCM"
    xh: str


class ElectricityAccountListResponse(BaseModel):
    school: School = "HNUCM"
    accounts: list[ElectricityAccountResponse]


class ElectricityAccountCreate(BaseModel):
    school: School | None = None
    xh: str = Field(min_length=1, max_length=128)
    pwd: str = Field(min_length=1, max_length=512)

    @field_validator("xh")
    @classmethod
    def normalize_xh(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("xh 不能为空")
        return value


class ElectricityAccountUpdate(BaseModel):
    school: School | None = None
    xh: str | None = Field(default=None, min_length=1, max_length=128)
    pwd: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("xh")
    @classmethod
    def normalize_xh(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("xh 不能为空")
        return value


class AcademicAccountResponse(ElectricityAccountResponse):
    """Public academic-account representation; passwords are omitted."""


class AcademicAccountListResponse(BaseModel):
    school: School = "HNUCM"
    accounts: list[AcademicAccountResponse]


class AcademicAccountCreate(ElectricityAccountCreate):
    pass


class AcademicAccountUpdate(ElectricityAccountUpdate):
    pass
