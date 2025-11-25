import pytest
from unittest.mock import patch, AsyncMock
from pathlib import Path
from main import get_kafka_config
from utils.log import logger

# 脚本当前的绝对路径
SCRIPT_DIR = Path(__file__).parent


@pytest.mark.asyncio
async def test_get_kafka_config_with_nacos_success():
    """测试Nacos配置获取成功的场景"""
    # 模拟成功的Nacos配置返回
    mock_config_client = AsyncMock()
    mock_config_client.get_config.return_value = '{"bootstrap_servers": "localhost:9092", "input_topic": "test-input", "output_topic": "test-output", "group_id": "test-group"}'
    
    with patch('main.get_nacos_config_client', return_value=mock_config_client):
        kafka_config = await get_kafka_config()
        
        # 验证返回的配置不为None
        assert kafka_config is not None
        
        # 验证返回的是字典类型
        assert isinstance(kafka_config, dict)
        
        # 验证必要的Kafka配置项存在
        expected_keys = ['bootstrap_servers', 'input_topic', 'output_topic', 'group_id']
        for key in expected_keys:
            assert key in kafka_config, f"Missing expected key: {key}"
            
        # 验证具体的配置值
        assert kafka_config['bootstrap_servers'] == 'localhost:9092'
        assert kafka_config['input_topic'] == 'test-input'
        assert kafka_config['output_topic'] == 'test-output'
        assert kafka_config['group_id'] == 'test-group'


@pytest.mark.asyncio
async def test_get_kafka_config_nacos_client_none():
    """测试Nacos客户端为None的情况"""
    with patch('main.get_nacos_config_client', return_value=None):
        kafka_config = await get_kafka_config()
        
        # 验证返回默认配置
        assert kafka_config is not None
        assert isinstance(kafka_config, dict)
        
        # 验证默认配置项
        expected_keys = ['bootstrap_servers', 'input_topic', 'output_topic', 'group_id']
        for key in expected_keys:
            assert key in kafka_config, f"Missing expected key: {key}"
            
        # 验证默认值
        assert kafka_config['bootstrap_servers'] == 'localhost:9092'
        assert kafka_config['group_id'] == 'atri-crawler-group'


@pytest.mark.asyncio
async def test_get_kafka_config_nacos_exception():
    """测试Nacos配置获取异常的情况"""
    mock_config_client: AsyncMock = AsyncMock()
    mock_config_client.get_config.side_effect = Exception("Nacos connection failed")
    
    with patch('main.get_nacos_config_client', return_value=mock_config_client):
        kafka_config = await get_kafka_config()
        
        # 验证返回默认配置
        assert kafka_config is not None
        assert isinstance(kafka_config, dict)
        
        # 验证默认配置项
        expected_keys = ['bootstrap_servers', 'input_topic', 'output_topic', 'group_id']
        for key in expected_keys:
            assert key in kafka_config, f"Missing expected key: {key}"
            
        # 验证默认值
        assert kafka_config['bootstrap_servers'] == 'localhost:9092'
        assert kafka_config['group_id'] == 'atri-crawler-group'


@pytest.mark.asyncio
async def test_get_kafka_config_empty_config_string():
    """测试Nacos返回空配置字符串的情况"""
    mock_config_client = AsyncMock()
    mock_config_client.get_config.return_value = ''
    
    with patch('main.get_nacos_config_client', return_value=mock_config_client):
        kafka_config = await get_kafka_config()
        
        # 验证返回默认配置
        assert kafka_config is not None
        assert isinstance(kafka_config, dict)
        
        # 验证默认配置项
        expected_keys = ['bootstrap_servers', 'input_topic', 'output_topic', 'group_id']
        for key in expected_keys:
            assert key in kafka_config, f"Missing expected key: {key}"