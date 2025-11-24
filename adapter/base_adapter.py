from abc import ABC, abstractmethod
from typing import Dict, List, Tuple
from httpx import Cookies
import asyncio

headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.5",
    "Connection": "keep-alive",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0",
}


class JWAdapter(ABC):
    """
    所有教务系统适配器必须实现此接口
    """

    def __init__(
        self,
        base_url: str,
        headers: Dict[str, str] = headers,
        timeout: int = 10,
        school_name: str = "BASE",
    ):
        self.base_url = base_url.rstrip("/")
        self.school_name = school_name
        self.timeout = timeout
        self.headers = headers

    @abstractmethod
    async def login(self, username: str, password: str) -> Tuple[bool, str, Cookies]:
        """
        从缓存读取Cookies，判断是否有效，有效则返回，否则进行登录教务系统

        Args:
            username (str): 用户名
            password (str): 密码

        Returns:
            Tuple[bool, str, Cookies]: 登录结果、消息和Cookies
        """
        pass

    @abstractmethod
    async def get_profile(
        self, cookies: Cookies, username: str
    ) -> Tuple[bool, str, Dict]:
        """
        获取用户个人信息

        Args:
            cookies (Cookies): 登录后的Cookies
            username (str): 用户名，用于日志标识

        Returns:
            Tuple[bool, str, Dict]: 获取结果、消息和用户信息字典
        """
        pass

    @abstractmethod
    async def get_grades(
        self, cookies: Cookies, username: str, semester: str = ""
    ) -> Tuple[bool, str, List[Dict]]:
        """
        获取成绩列表

        Args:
            cookies (Cookies): 登录后的Cookies
            username (str): 用户名，用于日志标识
            semester (str, optional): 学期标识符，默认为空

        Returns:
            Tuple[bool, str, List[Dict]]: 获取结果、消息和成绩数据列表
        """
        pass

    @abstractmethod
    async def get_course_schedule(
        self, cookies: Cookies, username: str, semester: str = ""
    ) -> Tuple[bool, str, List[Dict]]:
        """
        获取课表列表

        Args:
            cookies (Cookies): 登录后的Cookies
            username (str): 用户名，用于日志标识
            semester (str, optional): 学期标识符，默认为空

        Returns:
            Tuple[bool, str, List[Dict]]: 获取结果、消息和课表数据列表
        """
        pass
