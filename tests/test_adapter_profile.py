import pytest
import pytest_asyncio
from pathlib import Path
from adapter.hnucm_adapter import HNUCMAdapter
from adapter.hnucm_adapter.profile import ProfileCrawler
from utils.log import logger
import dotenv
import os

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
dotenv.load_dotenv(PROJECT_DIR / ".env.test")


class TestHNUCMAdapter:
    username: str = os.getenv("HNUCM_ADAPTER_TEST_USERNAME") or ""
    password: str = os.getenv("HNUCM_ADAPTER_TEST_PASSWORD") or ""

    @pytest_asyncio.fixture
    async def adapter(self):
        return HNUCMAdapter()

    @pytest.mark.asyncio
    async def test_fetch_profile_xls(self, adapter: HNUCMAdapter) -> None:
        success, message, cookies = await adapter.login(self.username, self.password)
        if not success:
            pytest.skip(f"登录失败，跳过测试: {message}")

        profile_crawler = ProfileCrawler(
            base_url=adapter.base_url,
            headers=adapter.headers,
            school_name=adapter.school_name,
            username=self.username,
        )
        xls_path: Path = await profile_crawler.fetch_profile_xls(cookies)

        # 断言
        assert xls_path.exists(), "xls 文件不存在"
        assert xls_path.is_file(), "xls_path 不是文件"
        assert xls_path.suffix == ".xls", "xls 文件后缀不是 .xls"

    @pytest.mark.asyncio
    async def test_parse_profile_xls(self, adapter: HNUCMAdapter) -> None:
        profile_crawler = ProfileCrawler(
            base_url=adapter.base_url,
            headers=adapter.headers,
            school_name=adapter.school_name,
            username=self.username,
        )
        xls_path: Path = SCRIPT_DIR / "fixtures" / "HNUCM_202301020304_profile.xls"
        profile: dict = await profile_crawler.parse_profile_xls(xls_path)

        logger.debug(profile)

        # 断言
        assert profile["department"] == "信息科学与工程学院", "院系解析错误"
        assert profile["major"] == "计算机科学与技术", "专业解析错误"
        assert profile["class_name"] == "测试班级", "班级解析错误"
        assert profile["name"] == "测试", "姓名解析错误"
        assert profile["gender"] == "男", "性别解析错误"

    @pytest.mark.asyncio
    async def test_profile_crawler(self, adapter: HNUCMAdapter) -> None:

        _, _, cookies = await adapter.login(self.username, self.password)
        if not cookies:
            pytest.skip("登录失败，跳过测试")

        _, _, profile = await adapter.get_profile(
            cookies=cookies, username=self.username
        )

        logger.debug(profile)

        # 断言
        assert profile.gender == "男", "性别解析错误"
