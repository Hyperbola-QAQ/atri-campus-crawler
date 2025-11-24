from typing import Dict
from httpx import Cookies
from pathlib import Path
import httpx
import xlrd
from schemas.profile_schema import Profile
from utils.log import logger
import aiofiles


class ProfileCrawler:
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

    async def fetch_profile_xls(self, cookies: Cookies) -> Path:
        """从教务系统获取个人信息 Excel 文件"""
        try:
            logger.debug(f"[{self.username}] 获取个人信息表")
            async with httpx.AsyncClient(
                base_url=self.base_url, cookies=cookies, headers=self.headers
            ) as client:
                response = await client.post("/jsxsd/grxx/xsxx_print.do")

            response.raise_for_status()

            # 保存文件到tmp/目录
            xls_path = Path(f"tmp/{self.school_name}_{self.username}_profile.xls")
            async with aiofiles.open(xls_path, "wb") as f:
                await f.write(response.content)
            logger.debug(f"[{self.username}] 个人信息表已保存至 {xls_path}")

            return xls_path
        except httpx.HTTPStatusError as e:
            logger.error(f"[{self.username}] 获取个人信息表失败，HTTP错误：{e}")
            raise ValueError(f"[{self.username}] 获取个人信息表失败，HTTP错误：{e}")
        except Exception as e:
            logger.error(f"[{self.username}] 获取个人信息表失败：{e}")
            raise ValueError(f"[{self.username}] 获取个人信息表失败：{e}")

    async def parse_profile_xls(self, xls_path: Path) -> Dict[str, str]:
        """解析个人信息 Excel 文件"""
        try:
            logger.debug(f"[{self.username}] 解析个人信息表 {xls_path}")

            # 读取 Excel 文件 第1份sheet
            # 第2行第1列是院系 第3列是专业 第6列是班级
            # 第3行第2列是姓名 第4列是性别
            async with aiofiles.open(xls_path, "rb") as f:
                content = await f.read()
            workbook = xlrd.open_workbook(file_contents=content)
            sheet = workbook.sheet_by_index(0)
            rows = [sheet.row_values(r) for r in range(sheet.nrows)]
            profile = {
                "department": str(rows[2][0]).strip().replace("院系：", ""),  # 院系
                "major": str(rows[2][2]).strip().replace("专业：", ""),  # 专业
                "class_name": str(rows[2][5]).strip().replace("班级：", ""),  # 班级
                "name": str(rows[3][1]).strip(),  # 姓名
                "gender": str(rows[3][3]).strip(),  # 性别
            }

            return profile
        except IndexError as e:
            logger.error(f"[{self.username}] 解析个人信息表失败，行/列索引越界：{e}")
            raise ValueError(f"[{self.username}] 获取个人信息失败：{e}")
        except Exception as e:
            logger.error(f"[{self.username}] 解析个人信息表失败：{e}")
            raise ValueError("Excel 格式不符合预期，请检查文件内容")

    async def get_profile_from_jwxt(self, cookies: Cookies) -> Profile:
        """从 Excel 文件中提取个人信息"""

        try:
            xls_path: Path = await self.fetch_profile_xls(cookies)
            profile: Dict = await self.parse_profile_xls(xls_path)

            # 转换为 Profile 模型
            return Profile(**profile)

        except IndexError:
            raise

        except Exception:
            raise
