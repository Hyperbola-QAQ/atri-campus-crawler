import math
from typing import Optional


def safe_float(
    value: Optional[str], default: Optional[float] = None
) -> Optional[float]:
    """数字或百分比转有限浮点数，百分比除以 100；空值、转换失败及非有限值返回 default。"""
    if value is None:
        return default
    try:
        value = value.strip() if isinstance(value, str) else value
        if isinstance(value, str) and value.endswith("%"):
            result = float(value[:-1]) / 100
        else:
            result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError, OverflowError):
        return default


def safe_int(value: Optional[str], default: Optional[int] = None) -> Optional[int]:
    """先转 float 再向零截断，转换失败返回 default；不代替业务范围校验。"""
    if value is None:
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError, OverflowError):
        return default
