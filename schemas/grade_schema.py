from pydantic import BaseModel
from typing import Optional


class GradeItem(BaseModel):
    """总成绩保留中文等级，缺失分项不等于零；开课与补考学期保留各自来源。
    课程代码可缺失，名称不能据此视为唯一。
    """

    course_code: Optional[str] = None
    course_name: str
    course_credit: float
    total_hours: Optional[int] = None
    lab_score: Optional[float] = None
    lab_ratio: Optional[float] = None
    midterm_score: Optional[float] = None
    midterm_ratio: Optional[float] = None
    regular_score: Optional[float] = None
    regular_ratio: Optional[float] = None
    final_score: Optional[float] = None
    final_ratio: Optional[float] = None
    total_score: str
    grade_mark: Optional[str] = None
    grade_point: float
    offered_semester: str
    retake_semester: Optional[str] = None
    assessment_method: Optional[str] = None
    exam_type: Optional[str] = None
    course_attribute: Optional[str] = None
    course_nature: Optional[str] = None
    general_elective_category: Optional[str] = None
    general_elective_module: Optional[str] = None
