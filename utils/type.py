import math
from typing import Optional


def safe_float(
    value: Optional[str], default: Optional[float] = None
) -> Optional[float]:
    """安全地将字符串转换为 float，失败时返回 default；还能处理 50% 这样的格式"""
    if value is None:
        return default
    try:
        # 去掉首尾空格
        value = value.strip() if isinstance(value, str) else value
        # 如果以 % 结尾，去掉 % 并除以 100
        if isinstance(value, str) and value.endswith("%"):
            result = float(value[:-1]) / 100
        else:
            result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError, OverflowError):
        return default


def safe_int(value: Optional[str], default: Optional[int] = None) -> Optional[int]:
    """安全地将字符串转换为 int，失败时返回 default"""
    if value is None:
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError, OverflowError):
        return default
