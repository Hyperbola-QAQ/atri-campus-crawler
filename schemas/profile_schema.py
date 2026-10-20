from pydantic import BaseModel
from typing import Optional


class Profile(BaseModel):
    """资料结构不负责身份绑定；gender 允许 None，但构造时仍须提供。"""

    department: str
    major: str
    class_name: str
    name: str
    gender: Optional[str]
