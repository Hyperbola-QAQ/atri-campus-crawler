from pydantic import BaseModel
from typing import Optional
from enum import Enum


class WeekdayEnum (str, Enum):
    MONDAY = '1'
    TUESDAY = '2'
    WEDNESDAY = '3'
    THURSDAY = '4'
    FRIDAY = '5'
    SATURDAY = '6'
    SUNDAY = '7'

class CourseScheduleItem(BaseModel):
    # 课程名称
    course_name: str
    # 教师姓名
    teacher_name: Optional[str] = None
    # 教师职称
    teacher_title: Optional[str] = None
    # 星期几
    day_of_week: WeekdayEnum
    # 周次列表
    week_list: list[int]
    # 学期
    semester: str
    # 节次列表
    class_period: list[int]
    # 上课地点
    location: Optional[str] = None