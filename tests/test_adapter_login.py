import pytest
import pytest_asyncio
from httpx import Cookies
from pathlib import Path
from adapter.hnucm_adapter import HNUCMAdapter
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
    async def test_login_success(self, adapter: HNUCMAdapter):
        # 使用真实的登录请求
        success, message, cookies = await adapter.login(self.username, self.password)

        assert success is True
        assert message == "Success"
        assert isinstance(cookies, Cookies)
        # 处理可能存在多个同名cookie的情况
        has_jsessionid = any(cookie.name == "JSESSIONID" for cookie in cookies.jar)
        assert has_jsessionid is True

    @pytest.mark.asyncio
    async def test_login_failure(self, adapter: HNUCMAdapter):
        # 测试登录失败情况
        success, message, cookies = await adapter.login("wrong_user", "wrong_password")

        assert success is False
        assert "请检查用户名、密码" in message
        assert isinstance(cookies, Cookies)
