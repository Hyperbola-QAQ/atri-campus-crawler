from typing import Dict, List, Any
from httpx import Cookies
from pathlib import Path
import httpx
from schemas.course_schedule_schema import CourseScheduleItem
from utils.log import logger
import re
import datetime
import xlrd


# 获取当前学期
async def get_current_semester() -> str:
    """
    获取期望获取的学期，格式为 yyyy-yyyy-1 或 yyyy-yyyy-2

    例如：2023 年 7 月 1 日 至 2024 年 1 月 15 日 期望获取 2023-2024-2
    2024 年 1 月 16 日 至 2024 年 6 月 30 日 期望获取 2024-2025-1
    """
    today = datetime.date.today()
    year = today.year
    if today.month >= 7:
        # 7 月 1 日及以后，返回当前学年-下一学年-2
        return f"{year}-{year + 1}-2"
    else:
        # 1 月 16 日至 6 月 30 日，返回上一学年-当前学年-1
        return f"{year - 1}-{year}-1"


async def parse_weeks_to_list(week_str: str) -> List[int]:
    """
    解析周次字符串为周次列表
    支持格式: "1-11", "19", "1-9,11", "1-3,5-7,9-10,12-13,15-16" 等
    """
    weeks = []

    if "," in week_str:
        parts = week_str.split(",")
        for part in parts:
            if "-" in part:
                start, end = map(int, part.split("-"))
                weeks.extend(range(start, end + 1))
            else:
                weeks.append(int(part))
    elif "-" in week_str:
        start, end = map(int, week_str.split("-"))
        weeks.extend(range(start, end + 1))
    else:
        weeks.append(int(week_str))

    return weeks


async def parse_periods_to_list(class_period: str) -> List[int]:
    """
    解析节次为列表
    """
    periods = []
    period_str = class_period.replace("节", "")

    if "-" in period_str:
        parts = period_str.split("-")
        for part in parts:
            if part.isdigit():
                periods.append(int(part))
    elif period_str.isdigit():
        periods.append(int(period_str))

    return periods


async def parse_course_block(lines: List[str]) -> Dict[str, Any]:
    """
    解析单个课程块信息
    """
    if len(lines) < 2:
        raise ValueError("课程解析出错")

    # 倒数统计
    # -1是地点
    # -2是时间
    # -3是教师（可能不存在）
    # 剩下的是课程名

    # 倒数第一行是地点
    location = (
        lines[-1]
        .replace("教室", "")
        .replace("立德楼", "立1")
        .replace("修业楼", "修2")
        .replace("济世楼", "济3")
    )

    # 倒数第二行是时间信息（周次和节次）
    time_info = lines[-2]

    # 提取周次信息
    week_match = re.search(r"([\d\-,\s]+)\(?周?\)?", time_info)
    week_list = week_match.group(1) if week_match else ""

    # 提取节次信息
    period_match = re.search(r"\[(\d+(?:-\d+)*节)]", time_info)
    class_period = period_match.group(1) if period_match else ""

    # 初始化教师信息
    teacher_title = ""

    # 如果行数大于等于3，处理教师信息
    if len(lines) > 3:
        teacher_info = lines[-3]
        if teacher_info:  # 教师信息不为空
            teacher_name = teacher_info
            # 提取教师职称
            title_match = re.search(r"\((.*)\)", teacher_info)
            if title_match:
                # 判断括号内容是否为职称（不是"助教"或"分组"等字样）
                title = title_match.group(1)
                if not any(keyword in title for keyword in ["助教", "分组"]):
                    teacher_title = title
                    # 去掉职称部分的教师名
                    teacher_name = re.sub(r"\(.*\)", "", teacher_info).strip()
                elif "助教" in title:
                    teacher_title = "助教"
                    teacher_name = re.sub(r"\(.*\)", "", teacher_info).strip()

        else:
            # 教师信息为空的情况
            teacher_name = "未知教师"
    else:
        # 行数小于3的情况，标记为未知教师
        teacher_name = "未知教师"

    # 课程名是除了最后两行（时间、地点）和可能的教师行之外的所有行
    course_name_lines = lines[:1] if len(lines) <= 4 else lines[:2]
    course_name = "".join(course_name_lines)

    return {
        "course_name": course_name,
        "teacher_name": teacher_name,
        "teacher_title": (
            teacher_title if teacher_title and teacher_title != "无" else None
        ),
        "week_list": await parse_weeks_to_list(week_list) if week_list else [],
        "class_period": await parse_periods_to_list(
            class_period if class_period else ""
        ),
        "location": location if location else "",
    }


async def parse_cell_content(content: str) -> list[Dict[str, Any]]:
    """
    解析单元格中的课程内容
    每个单元格可能包含多门课程，用两个换行符分隔
    """
    courses = []

    # 按两个或多个换行符分割不同的课程
    course_blocks = re.split(r"\n{2,}", content.strip())

    # 合并处理lines长度小于3的情况
    merged_blocks = []
    i = 0
    while i < len(course_blocks):
        if not course_blocks[i].strip():
            i += 1
            continue

        lines = [line.strip() for line in course_blocks[i].split("\n") if line.strip()]
        # 如果当前块行数小于3，检查是否需要与后续块合并
        if len(lines) < 3:
            # 标记需要合并
            merged_content = course_blocks[i]
            i += 1
            # 继续检查后续块是否也需要合并
            while i < len(course_blocks):
                if not course_blocks[i].strip():
                    i += 1
                    continue
                next_lines = [
                    line.strip()
                    for line in course_blocks[i].split("\n")
                    if line.strip()
                ]
                if len(next_lines) < 3:
                    # 如果下一个块也小于3行，则合并
                    merged_content += "\n\n" + course_blocks[i]
                    i += 1
                else:
                    # 如果下一个块正常，则停止合并
                    break
            merged_blocks.append(merged_content)
        else:
            merged_blocks.append(course_blocks[i])
            i += 1

    for block in merged_blocks:
        if not block.strip():
            continue

        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        # 解析课程信息
        course_info = await parse_course_block(lines)
        if course_info:
            courses.append(course_info)

    return courses


class CourseScheduleCrawler:
    def __init__(
        self,
        base_url: str,
        headers: dict,
        school_name: str,
        username: str,
        timeout: int = 10,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.headers = headers
        self.school_name = school_name
        self.username = username

    async def fetch_course_schedule_xls(self, semester: str, cookies: Cookies) -> Path:
        """从教务系统获取课表 Excel 文件"""

        # 页面参数：学期格式为 yyyy-yyyy-1 或 yyyy-yyyy-2
        if semester:
            if re.fullmatch(r"^\d{4}-\d{4}-[12]$", semester):
                params = {"xnxq01id": semester}
            else:
                params = {
                    "xnxq01id": await get_current_semester()
                }  # 仅为兜底，实际前端请求中不应该出现

        try:
            logger.debug(f"[{self.username}] 获取课表")
            async with httpx.AsyncClient(
                base_url=self.base_url, cookies=cookies, headers=self.headers
            ) as client:
                response = await client.post("/jsxsd/xskb/xskb_print.do", params=params)

            response.raise_for_status()

            # 保存文件到tmp/目录
            xls_path = Path(
                f"tmp/{self.school_name}_{self.username}_course_schedule_{semester}.xls"
            )
            with open(xls_path, "wb") as f:
                f.write(response.content)
            logger.debug(f"[{self.username}] 课表已保存至 {xls_path}")

            return xls_path
        except httpx.HTTPStatusError as e:
            logger.error(f"[{self.username}] 获取课表失败,HTTP错误:{e}")
            raise ValueError(f"[{self.username}] 获取课表失败,HTTP错误:{e}")
        except Exception as e:
            logger.error(f"[{self.username}] 获取课表失败：{e}")
            raise ValueError(f"[{self.username}] 获取课表失败：{e}")

    async def parse_course_schedule_xls(self, xls_path: Path) -> List[Dict[str, Any]]:
        """解析课程表 Excel 文件"""
        try:
            logger.debug(f"[{self.username}] 解析课程表 {xls_path}")
            course_list: List[Dict[str, Any]] = []

            # 星期几映射：Excel 的 B~H 列 → 周一~周日
            weekday_map = {
                1: "1",  # B -> 周一
                2: "2",  # C -> 周二
                3: "3",  # D -> 周三
                4: "4",  # E -> 周四
                5: "5",  # F -> 周五
                6: "6",  # G -> 周六
                7: "7",  # H -> 周日
            }

            workbook = xlrd.open_workbook(str(xls_path))
            sheet = workbook.sheet_by_index(0)
            rows = [sheet.row_values(r) for r in range(sheet.nrows)]

            # 从第二行第一列提取学年学期
            # 学年学期：2025-2026-1        班级：测试        专业：计算机科学与技术        院系：信息科学与工程学院        打印日期：2025-11-23
            first_row_text = str(rows[1][0]).strip()
            # 使用正则提取“学年学期：”后的内容，直到遇到空格或制表符
            match = re.search(r"学年学期：([^\s]+)", first_row_text)
            if not match:
                logger.warning(
                    f"[{self.username}] 无法从Excel标题行解析学年学期：{first_row_text}"
                )
            semester = match.group(1) if match else "未知学期"

            # 检查至少有 8 列（A 到 H）
            if len(rows[0]) < 8:
                raise ValueError(f"[{self.username}] 至少需要 8 列(A-H)")

            # 获取目标行范围：第4行到倒数第2行（rows[3:-1]）
            # 注意：iloc 是 0 起始，所以第4行是 iloc[3]，倒数第2行是 iloc[-2]
            target_rows = rows[3:-1]

            for idx, row in enumerate(target_rows):
                # 遍历 B 到 H 列（位置索引 1 到 7）
                for col_idx in range(1, 8):
                    cell_value = row[col_idx]
                    if not str(cell_value).strip() or cell_value is None:
                        continue
                    day_of_week = weekday_map[col_idx]
                    cell_courses: List[Dict[str, Any]] = await parse_cell_content(
                        str(cell_value)
                    )
                    for course in cell_courses:
                        course.setdefault("day_of_week", day_of_week)
                        course.setdefault("semester", semester)
                        course_list.append(course)

            return course_list

        except IndexError as e:
            logger.error(f"[{self.username}] 解析课程表失败，行/列索引越界：{e}")
            raise ValueError(f"[{self.username}] 解析课程表失败：{e}")
        except Exception as e:
            logger.error(f"[{self.username}] 解析课程表失败：{e}")
            raise ValueError("Excel 格式不符合预期，请检查文件内容")

    async def get_course_schedule_from_jwxt(
        self, semester: str, cookies: Cookies
    ) -> List[CourseScheduleItem]:
        """从教务系统获取课表"""

        try:
            xls_path: Path = await self.fetch_course_schedule_xls(semester, cookies)
            course_list: List[Dict[str, Any]] = await self.parse_course_schedule_xls(
                xls_path
            )
            logger.debug(f"[{self.username}] 解析到 {len(course_list)} 条课程记录")

            return [CourseScheduleItem(**course) for course in course_list]

        except IndexError:
            raise

        except Exception:
            raise
