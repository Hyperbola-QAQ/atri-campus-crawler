from pydantic import BaseModel
from typing import Optional


class Profile(BaseModel):
    # 所属院系
    department: str
    # 专业
    major: str
    # 班级
    class_name: str
    # 姓名
    name: str
    # 性别（可选）
    gender: Optional[str]
