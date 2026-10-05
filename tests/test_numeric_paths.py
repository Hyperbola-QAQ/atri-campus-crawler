"""教务数值解析：空值、百分数、非法输入和非有限值。"""

import pytest
from utils.type import safe_float, safe_int


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, -1),
        ("", -1),
        ("invalid", -1),
        (" 50% ", 0.5),
        (" 2.5 ", 2.5),
        (12, 12.0),
        ([], -1),
        ({}, -1),
        ("NaN", -1),
        ("Infinity", -1),
    ],
)
def test_safe_float_boundary(value, expected):
    assert safe_float(value, default=-1) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, -1),
        ("", -1),
        ("invalid", -1),
        ("2.9", 2),
        ("-2.9", -2),
        (12, 12),
        ([], -1),
        ({}, -1),
        ("NaN", -1),
        ("Infinity", -1),
    ],
)
def test_safe_int_boundary(value, expected):
    assert safe_int(value, default=-1) == expected
