"""电费条件刷新比较；相同是重查条件，不是平台未更新的证明。"""

from decimal import Decimal, InvalidOperation


def same_reading(current: dict, previous: dict) -> bool:
    """同电表的有效余额、电量都相同；忽略数字格式与采集时间。"""
    meter = current.get("meter_number")
    if not isinstance(meter, str) or not meter.strip():
        return False
    if meter != previous.get("meter_number"):
        return False
    pairs = []
    for field in ("balance", "remaining_electricity"):
        a, b = current.get(field), previous.get(field)
        if a is None and b is None:
            continue
        try:
            a = Decimal(str(a).strip().lower().removesuffix("kwh").strip())
            b = Decimal(str(b).strip().lower().removesuffix("kwh").strip())
            if not a.is_finite() or not b.is_finite():
                return False
        except InvalidOperation:
            return False
        pairs.append(a == b)
    return bool(pairs) and all(pairs)
