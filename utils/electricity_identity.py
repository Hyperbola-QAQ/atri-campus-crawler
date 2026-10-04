"""电费寝室身份协议。"""

import re

ROOM_INPUT_PATTERN = r"^(?:(?:guojiao-|国教-?))?(?:[0-9]{4,5}|[0-9]{1,2}-[0-9]{3})$"
ROOM_PATTERN = r"^(?:guojiao-)?[0-9]{4,5}$"


def normalize_room_number(room_number: str) -> str:
    """保留楼栋类别, 将国教和普通寝室规范为不同的稳定标识。"""
    if re.fullmatch(ROOM_INPUT_PATTERN, room_number) is None:
        raise ValueError("room_number 格式不正确")
    special = room_number.startswith(("guojiao-", "国教"))
    number = re.sub(r"^(?:guojiao-|国教-?)", "", room_number)
    number = number.replace("-", "").zfill(5)
    return ("guojiao-" if special else "") + number


def numeric_room_number(room_number: str) -> str:
    """仅供展示、楼层过滤使用, 不能作为缓存或历史记录键。"""
    return normalize_room_number(room_number).removeprefix("guojiao-")
