from typing import Optional


def safe_float(value: Optional[str], default: Optional[float] = None) -> Optional[float]:
    """安全地将字符串转换为 float，失败时返回 default；还能处理 50% 这样的格式"""
    if value is None:
        return default
    try:
        # 去掉首尾空格
        value = value.strip()
        # 如果以 % 结尾，去掉 % 并除以 100
        if value.endswith('%'):
            return float(value[:-1]) / 100
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: Optional[str], default: Optional[int] = None) -> Optional[int]:
    """安全地将字符串转换为 int，失败时返回 default"""
    if value is None:
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default