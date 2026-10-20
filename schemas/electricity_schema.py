"""公开账户响应不含密码；结构校验不证明平台登录成功，正文学校须与路由范围一致。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from schemas.school import School


class ElectricityResponse(BaseModel):
    """身份使用校区和寝室，不能以显示名称替代；collected_at 是实际采集时刻。
    兼容旧载荷缺字段，电量单位文本及余额类型沿用平台，不猜测补齐。
    """

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
    """completed 仅表示所有目标已尝试，不保证读数齐全；queried 上界由断点服务约束。"""

    school: School = "HNUCM"
    collection_date: str
    total: int = Field(ge=0)
    queried: int = Field(ge=0)
    completed: bool


class ElectricityAccountResponse(BaseModel):
    """仅公开学校和用户名，不复用敏感创建请求，避免响应意外扩展。"""

    school: School = "HNUCM"
    xh: str


class ElectricityAccountListResponse(BaseModel):
    """列表归属指定学校，空列表不用于掩盖账户文件损坏。"""

    school: School = "HNUCM"
    accounts: list[ElectricityAccountResponse]


class ElectricityAccountCreate(BaseModel):
    """用户名去外层空白并须非空，密码原样保留；仅验证结构，不验证平台接受凭据。"""

    school: School | None = None
    xh: str = Field(min_length=1, max_length=128)
    pwd: str = Field(min_length=1, max_length=512)

    @field_validator("xh")
    @classmethod
    def normalize_xh(cls, value: str) -> str:
        """仅规范用户名并拒绝空白，不改变密码。"""
        value = value.strip()
        if not value:
            raise ValueError("xh 不能为空")
        return value


class ElectricityAccountUpdate(BaseModel):
    """None 保留原值，空白用户名仍拒绝；路由要求实际变更，正文学校不能移动账户归属。"""

    school: School | None = None
    xh: str | None = Field(default=None, min_length=1, max_length=128)
    pwd: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("xh")
    @classmethod
    def normalize_xh(cls, value: str | None) -> str | None:
        """None 表示不更新，其他输入去空白后须非空。"""
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("xh 不能为空")
        return value


class AcademicAccountResponse(ElectricityAccountResponse):
    """教务公开响应不含密码；复用结构不表示共享电量账户池或登录状态。"""


class AcademicAccountListResponse(BaseModel):
    """指定学校教务池的公开列表。"""

    school: School = "HNUCM"
    accounts: list[AcademicAccountResponse]


class AcademicAccountCreate(ElectricityAccountCreate):
    """复用凭据结构校验，实际教务存储由路由选择。"""

    pass


class AcademicAccountUpdate(ElectricityAccountUpdate):
    """教务更新沿用 None 保留原值的规则。"""

    pass
