import pytest
from pathlib import Path
from v2.nacos import ConfigParam
from utils.nacos import get_nacos_config_client
from utils.log import logger
import json

# 脚本当前的绝对路径
SCRIPT_DIR = Path(__file__).parent


@pytest.mark.asyncio
async def test_get_nacos_config_client():
    """测试获取Nacos配置客户端"""
    nacos_client = await get_nacos_config_client()
    assert nacos_client is not None
    # 尝试读取redis的配置
    # 获取配置
    config_param = ConfigParam(
        data_id="dev.redis.json",
        group="REDIS",
    )
    config_str = await nacos_client.get_config(config_param)
    if config_str:
        # 解析配置
        config = json.loads(config_str)

        logger.debug(config)
    # 断言配置
    assert config.get("host") == "127.0.0.1", "host 配置错误"
    assert config.get("port") == 6379, "port 配置错误"
    assert config.get("db") == 0, "db 配置错误"
    assert config.get("password") is None, "password 配置错误"
    assert config.get("decode_responses") is True, "decode_responses 配置错误"
    assert config.get("ssl") is False, "ssl 配置错误"
