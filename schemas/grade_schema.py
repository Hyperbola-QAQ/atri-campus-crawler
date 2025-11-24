from pydantic import BaseModel
from typing import Optional


class GradeItem(BaseModel):
    # 课程代码
    course_code: Optional[str] = None
    # 课程名称
    course_name: str
    # 课程学分
    course_credit: float
    # 总学时
    total_hours: Optional[int] = None
    # 实验成绩
    lab_score: Optional[float] = None
    # 实验成绩占比
    lab_ratio: Optional[float] = None
    # 期中成绩
    midterm_score: Optional[float] = None
    # 期中成绩占比
    midterm_ratio: Optional[float] = None
    # 平时成绩
    regular_score: Optional[float] = None
    # 平时成绩占比
    regular_ratio: Optional[float] = None
    # 期末成绩
    final_score: Optional[float] = None
    # 期末成绩占比
    final_ratio: Optional[float] = None
    # 总成绩
    total_score: str
    # 成绩标记
    grade_mark: Optional[str] = None
    # 绩点
    grade_point: float
    # 开课学期
    offered_semester: str
    # 补考学期
    retake_semester: Optional[str] = None
    # 考核方式
    assessment_method: Optional[str] = None
    # 考试类型
    exam_type: Optional[str] = None
    # 课程属性
    course_attribute: Optional[str] = None
    # 课程性质
    course_nature: Optional[str] = None
    # 通识选修课类别
    general_elective_category: Optional[str] = None
    # 通识选修课模块
    general_elective_module: Optional[str] = None
