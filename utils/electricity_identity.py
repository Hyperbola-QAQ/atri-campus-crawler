"""电费寝室身份协议。"""

import re

ROOM_INPUT_PATTERN = r"^(?:(?:guojiao-|国教-?))?(?:[0-9]{4,5}|[0-9]{1,2}-[0-9]{3})$"
ROOM_PATTERN = r"^(?:guojiao-)?[0-9]{4,5}$"


def normalize_room_number(room_number: str) -> str:
    """支持四五位数字及楼栋分隔写法，补五位并保留 guojiao- 楼类身份；非法输入抛 ValueError。
    普通与国教同号不可共用缓存或历史键。
    """
    if re.fullmatch(ROOM_INPUT_PATTERN, room_number) is None:
        raise ValueError("room_number 格式不正确")
    special = room_number.startswith(("guojiao-", "国教"))
    number = re.sub(r"^(?:guojiao-|国教-?)", "", room_number)
    number = number.replace("-", "").zfill(5)
    return ("guojiao-" if special else "") + number


def numeric_room_number(room_number: str) -> str:
    """先校验再取五位数字供展示或楼层过滤；丢失楼类，不可作为查询目标或身份键。"""
    return normalize_room_number(room_number).removeprefix("guojiao-")
