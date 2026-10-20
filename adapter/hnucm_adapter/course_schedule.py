"""按打印 Excel 模板解析，星期来自 B—H 列，学期来自标题；抓取路径负责临时文件清理。"""

from typing import Dict, List, Any
from httpx import Cookies
from pathlib import Path
import httpx
from schemas.course_schedule_schema import CourseScheduleItem
from utils.log import logger
import re
import datetime
import xlrd
import tempfile


async def get_current_semester() -> str:
    """沿用本地日期的月份规则：7—12 月为本年起始学年第二学期，1—6 月为上一学年第一学期。
    不读取校历，与 server 的日级学期边界不同。
    """
    today = datetime.date.today()
    year = today.year
    if today.month >= 7:
        # 7 月 1 日及以后，返回当前学年-下一学年-2
        return f"{year}-{year + 1}-2"
    else:
        # 一月至六月按现有月份规则使用上一学年的第一学期。
        return f"{year - 1}-{year}-1"


async def parse_weeks_to_list(week_str: str) -> List[int]:
    """展开逗号和闭区间，保留输入顺序及重复；非法数字抛错，不推断单双周。"""
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
    """连字符仅分隔已列节号，1-3 返回第一、三节，不展开区间；无法识别部分忽略。"""
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
    """按尾部地点、时间及可选教师解析，课程名取前一或两行；不足两行抛 ValueError。
    教师分组文字保留，无时间匹配则周次、节次为空，不猜数字排课。
    """
    if len(lines) < 2:
        raise ValueError("课程解析出错")

    location = (
        lines[-1]
        .replace("教室", "")
        .replace("立德楼", "立1")
        .replace("修业楼", "修2")
        .replace("济世楼", "济3")
    )

    time_info = lines[-2]

    week_match = re.search(r"([\d\-,\s]+)\(?周?\)?", time_info)
    week_list = week_match.group(1) if week_match else ""

    period_match = re.search(r"\[(\d+(?:-\d+)*节)]", time_info)
    class_period = period_match.group(1) if period_match else ""

    teacher_title = ""

    # 如果行数大于等于3，处理教师信息
    if len(lines) > 3:
        teacher_info = lines[-3]
        if teacher_info:  # 教师信息不为空
            teacher_name = teacher_info
            title_match = re.search(r"\((.*)\)", teacher_info)
            if title_match:
                # 判断括号内容是否为职称（不是"助教"或"分组"等字样）
                title = title_match.group(1)
                if not any(keyword in title for keyword in ["助教", "分组"]):
                    teacher_title = title
                    teacher_name = re.sub(r"\(.*\)", "", teacher_info).strip()
                elif "助教" in title:
                    teacher_title = "助教"
                    teacher_name = re.sub(r"\(.*\)", "", teacher_info).strip()

        else:
            teacher_name = "未知教师"
    else:
        # 行数小于3的情况，标记为未知教师
        teacher_name = "未知教师"

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
    """按空行拆块并合并相邻不足三行的片段，完整块保持独立；空白返回空列表。"""
    courses = []

    course_blocks = re.split(r"\n{2,}", content.strip())

    merged_blocks = []
    i = 0
    while i < len(course_blocks):
        if not course_blocks[i].strip():
            i += 1
            continue

        lines = [line.strip() for line in course_blocks[i].split("\n") if line.strip()]
        # 如果当前块行数小于3，检查是否需要与后续块合并
        if len(lines) < 3:
            merged_content = course_blocks[i]
            i += 1
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
                    merged_content += "\n\n" + course_blocks[i]
                    i += 1
                else:
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

        course_info = await parse_course_block(lines)
        if course_info:
            courses.append(course_info)

    return courses


class CourseScheduleCrawler:
    """借用登录 Cookie，学期以文件标题为准；抓取路径清理文件，单独解析不删除调用方输入。"""

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
        """下载到独立系统临时文件，由调用方删除；非空非法学期回退月份规则。
        调用方须传非空学期，下载异常转 ValueError；不修正既有空参数行为。
        """

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

            with tempfile.NamedTemporaryFile(suffix=".xls", delete=False) as f:
                xls_path = Path(f.name)
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
        """按首张表八列模板解析第四行至倒数第二行，B—H 映射周一至周日。
        第二行标题缺学期时标记未知，不以请求值冒充来源；格式或索引错误转 ValueError。
        """
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

            # 学年学期：2025-2026-1        班级：测试        专业：计算机科学与技术        院系：信息科学与工程学院        打印日期：2025-11-23
            first_row_text = str(rows[1][0]).strip()
            match = re.search(r"学年学期：([^\s]+)", first_row_text)
            if not match:
                logger.warning(
                    f"[{self.username}] 无法从Excel标题行解析学年学期：{first_row_text}"
                )
            semester = match.group(1) if match else "未知学期"

            if len(rows[0]) < 8:
                raise ValueError(f"[{self.username}] 至少需要 8 列(A-H)")

            target_rows = rows[3:-1]

            for idx, row in enumerate(target_rows):
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
        """下载后解析无论成败都删除临时文件，再构造课程模型；异常向外传播。"""

        try:
            xls_path: Path = await self.fetch_course_schedule_xls(semester, cookies)
            try:
                course_list: List[
                    Dict[str, Any]
                ] = await self.parse_course_schedule_xls(xls_path)
            finally:
                xls_path.unlink(missing_ok=True)
            logger.debug(f"[{self.username}] 解析到 {len(course_list)} 条课程记录")

            return [CourseScheduleItem(**course) for course in course_list]

        except IndexError:
            raise

        except Exception:
            raise
