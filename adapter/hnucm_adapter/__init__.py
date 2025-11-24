from typing import Dict, List, Tuple
from httpx import Cookies
from adapter.base_adapter import JWAdapter
from schemas.grade_schema import GradeItem
from schemas.profile_schema import Profile
from .profile import ProfileCrawler
from .auth import get_valid_cookies
from .grade import GradeCrawler
from .course_schedule import CourseScheduleCrawler, CourseScheduleItem
from utils.log import logger

# 请求头
headers: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.5",
    "Connection": "keep-alive",
    "Host": "jwxt.hnucm.edu.cn",
    "Referer": "https://jwxt.hnucm.edu.cn/jsxsd/kscj/cjcx_frm",
    "Sec-Fetch-Dest": "iframe",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0",
}


class HNUCMAdapter(JWAdapter):
    """
    湖南中医药大学教务系统适配器实现
    """

    def __init__(
        self,
        base_url: str = "https://jwxt.hnucm.edu.cn",
        school_name: str = "HNUCM",
        timeout: int = 10,
        headers: dict[str, str] = headers,
    ):
        super().__init__(
            base_url=base_url, timeout=timeout, school_name=school_name, headers=headers
        )
        self.headers = headers
        self.school_name = school_name

    async def login(self, username: str, password: str) -> Tuple[bool, str, Cookies]:
        """
        获取有效Cookies

        Args:
            username (str): 用户名
            password (str): 密码

        Returns:
            Tuple[bool, str, Cookies]: 登录结果、消息和Cookies
        """
        try:
            cookies = await get_valid_cookies(
                base_url=self.base_url,
                school_name=self.school_name,
                timeout=self.timeout,
                headers=self.headers,
                username=username,
                password=password,
            )
            return True, "Success", cookies
        except Exception as e:
            return False, str(e), Cookies()

    async def get_profile(
        self, cookies: Cookies, username: str
    ) -> Tuple[bool, str, Profile]:
        """
        获取用户个人信息

        Args:
            cookies (Cookies): 登录后的Cookies
            username (str): 用户名，用于日志标识

        Returns:
            Tuple[bool, str, Profile]: 获取结果、消息和用户信息模型
        """
        try:
            logger.info(f"[{username}] 开始获取用户个人信息")
            profile_crawler = ProfileCrawler(
                base_url=self.base_url,
                headers=self.headers,
                school_name=self.school_name,
                username=username,
            )
            profile: Profile = await profile_crawler.get_profile_from_jwxt(cookies)
            logger.info(f"[{username}] 获取用户个人信息成功")
            return True, "Success", profile
        except Exception as e:
            logger.error(f"[{username}] 获取用户个人信息失败: {str(e)}")
            return False, str(e), {}

    async def get_grades(
        self, cookies: Cookies, username: str, semester: str = ""
    ) -> Tuple[bool, str, List[GradeItem]]:
        """
        获取成绩信息

        Args:
            cookies (Cookies): 登录后的Cookies
            username (str): 用户名，用于日志标识
            semester (str, optional): 学期标识符

        Returns:
            Tuple[bool, str, List[Dict]]: 获取结果、消息和成绩数据列表
        """
        try:
            logger.info(f"[{username}] 开始获取成绩信息，学期: {semester}")
            grade_crawler = GradeCrawler(
                base_url=self.base_url,
                headers=self.headers,
                username=username,
            )

            grades: List[GradeItem] = await grade_crawler.get_grades_from_jwxt(
                cookies=cookies,
                semester=semester,
            )
            logger.info(f"[{username}] 获取成绩信息成功，共 {len(grades)} 条记录")
            return True, "Success", grades
        except Exception as e:
            logger.error(f"[{username}] 获取成绩信息失败: {str(e)}")
            return False, str(e), []

    async def get_course_schedule(
        self, cookies: Cookies, username: str, semester: str = ""
    ) -> Tuple[bool, str, List[Dict]]:
        """
        获取课表信息

        Args:
            cookies (Cookies): 登录后的Cookies
            username (str): 用户名，用于日志标识
            semester (str, optional): 学期标识符

        Returns:
            Tuple[bool, str, List[Dict]]: 获取结果、消息和课表数据列表
        """
        try:
            logger.info(f"[{username}] 开始获取课表信息，学期: {semester}")
            course_schedule_crawler = CourseScheduleCrawler(
                base_url=self.base_url,
                headers=self.headers,
                school_name=self.school_name,
                username=username,
            )
            # 由于course_schedule模块还未异步化，这里使用执行器来运行同步函数
            schedules: List[CourseScheduleItem] = (
                await course_schedule_crawler.get_course_schedule_from_jwxt(
                    cookies=cookies,
                    semester=semester,
                )
            )
            logger.info(f"[{username}] 获取课表信息成功")
            return True, "Success", schedules
        except Exception as e:
            logger.error(f"[{username}] 获取课表信息失败: {str(e)}")
            return False, str(e), []
