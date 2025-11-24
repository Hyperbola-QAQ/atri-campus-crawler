from dotenv import load_dotenv
import os
from v2.nacos import (
    NacosNamingService,
    ClientConfigBuilder,
    GRPCConfig,
    Instance,
    SubscribeServiceParam,
    RegisterInstanceParam,
    DeregisterInstanceParam,
    BatchRegisterInstanceParam,
    GetServiceParam,
    ListServiceParam,
    ListInstanceParam,
    NacosConfigService,
    ConfigParam,
)


load_dotenv(".env")
# 再根据环境变量加载对应的.env文件
load_dotenv(f'.env.{os.getenv("ENVIRONMENT")}')

client_config = (
    ClientConfigBuilder()
    .username(os.getenv("NACOS_USERNAME"))
    .password(os.getenv("NACOS_PASSWORD"))
    .server_address(os.getenv("NACOS_SERVER_ADDR", "localhost:8848"))
    .log_level("INFO")
    .namespace_id(os.getenv("NACOS_NAMESPACE_ID", "ATRI-crawler"))
    .cache_dir(os.getenv("NACOS_CACHE_DIR", "./nacos_cache"))
    .grpc_config(GRPCConfig(grpc_timeout=5000))
    .build()
)


async def get_nacos_config_client():
    """获取Nacos配置客户端"""
    return await NacosConfigService.create_config_service(client_config)
