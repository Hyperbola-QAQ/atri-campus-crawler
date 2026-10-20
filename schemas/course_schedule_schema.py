from pydantic import BaseModel
from typing import Optional
from enum import Enum


class WeekdayEnum(str, Enum):
    """教务星期使用字符串 1—7，不采用 Python 零起始 weekday。"""

    MONDAY = "1"
    TUESDAY = "2"
    WEDNESDAY = "3"
    THURSDAY = "4"
    FRIDAY = "5"
    SATURDAY = "6"
    SUNDAY = "7"


class CourseScheduleItem(BaseModel):
    """同一单元格多门课各自成记录，不按课程名或星期合并；周次、节次已展开。
    学期保留打印来源，模型不判断时间冲突。
    """

    course_name: str
    teacher_name: Optional[str] = None
    teacher_title: Optional[str] = None
    day_of_week: WeekdayEnum
    week_list: list[int]
    semester: str
    class_period: list[int]
    location: Optional[str] = None
