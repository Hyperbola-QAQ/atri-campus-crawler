"""按教务打印模板的固定坐标提取个人资料，下载文件仅作临时解析输入。"""

from typing import Dict
from httpx import Cookies
from pathlib import Path
import httpx
import xlrd
from schemas.profile_schema import Profile
from utils.log import logger
import tempfile


class ProfileCrawler:
    """使用调用方提供的登录 Cookie 查询资料，字段约束由 Profile 模型验证。"""

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
        """返回调用方负责删除的临时 Excel 路径；下载或写入失败转为 ValueError。
        使用系统临时目录兼容只读部署，文件包含个人资料，不应长期保留。
        """
        try:
            logger.debug(f"[{self.username}] 获取个人信息表")
            async with httpx.AsyncClient(
                base_url=self.base_url, cookies=cookies, headers=self.headers
            ) as client:
                response = await client.post("/jsxsd/grxx/xsxx_print.do")

            response.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".xls", delete=False) as f:
                xls_path = Path(f.name)
                f.write(response.content)
            logger.debug(f"[{self.username}] 个人信息表已保存至 {xls_path}")

            return xls_path
        except httpx.HTTPStatusError as e:
            logger.error(f"[{self.username}] 获取个人信息表失败，HTTP错误：{e}")
            raise ValueError(f"[{self.username}] 获取个人信息表失败，HTTP错误：{e}")
        except Exception as e:
            logger.error(f"[{self.username}] 获取个人信息表失败：{e}")
            raise ValueError(f"[{self.username}] 获取个人信息表失败：{e}")

    async def parse_profile_xls(self, xls_path: Path) -> Dict[str, str]:
        """按第一张工作表的固定坐标提取资料，不负责删除文件。
        模板缺行缺列或格式错误转为 ValueError。
        """
        try:
            logger.debug(f"[{self.username}] 解析个人信息表 {xls_path}")

            content = xls_path.read_bytes()
            workbook = xlrd.open_workbook(file_contents=content)
            sheet = workbook.sheet_by_index(0)
            rows = [sheet.row_values(r) for r in range(sheet.nrows)]
            profile = {
                "department": str(rows[2][0]).strip().replace("院系：", ""),
                "major": str(rows[2][2]).strip().replace("专业：", ""),
                "class_name": str(rows[2][5]).strip().replace("班级：", ""),
                "name": str(rows[3][1]).strip(),
                "gender": str(rows[3][3]).strip(),
            }

            return profile
        except IndexError as e:
            logger.error(f"[{self.username}] 解析个人信息表失败，行/列索引越界：{e}")
            raise ValueError(f"[{self.username}] 获取个人信息失败：{e}")
        except Exception as e:
            logger.error(f"[{self.username}] 解析个人信息表失败：{e}")
            raise ValueError("Excel 格式不符合预期，请检查文件内容")

    async def get_profile_from_jwxt(self, cookies: Cookies) -> Profile:
        """下载后无论解析成功与否都清理临时文件，再构造 Profile；异常向上传播。"""

        try:
            xls_path: Path = await self.fetch_profile_xls(cookies)
            try:
                profile: Dict = await self.parse_profile_xls(xls_path)
            finally:
                xls_path.unlink(missing_ok=True)

            return Profile(**profile)

        except IndexError:
            raise

        except Exception:
            raise
