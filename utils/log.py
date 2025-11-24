# utils/log.py

from loguru import logger
import sys
from pathlib import Path

# ----------------------------
# 日志配置
# ----------------------------

# 移除默认的 handler（控制台输出），我们将自定义
logger.remove()

# 项目根目录（log.py 在 utils 目录下）
PROJECT_ROOT = Path(__file__).parent.parent

# 日志文件目录
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)  # 确保 logs 目录存在

# ----------------------------
# 添加 Handler
# ----------------------------

# 控制台 Handler
logger.add(
    sink=sys.stdout,
    level="DEBUG",
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)


# DEBUG 级别日志文件
logger.add(
    sink=LOGS_DIR / "app_debug.log",
    level="DEBUG",
    rotation="100 MB",  # 每 100MB 创建一个新文件
    retention="7 days",  # 保留最近 7 天的日志
    compression="zip",  # 压缩旧日志
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
)


# INFO 级别日志文件
logger.add(
    sink=LOGS_DIR / "app_info.log",
    level="INFO",
    rotation="100 MB",  # 每 100MB 创建一个新文件
    retention="7 days",  # 保留最近 7 天的日志
    compression="zip",  # 压缩旧日志
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
)

# ERROR 级别日志文件（只记录 ERROR 及以上级别）
logger.add(
    sink=LOGS_DIR / "app_error.log",
    level="ERROR",
    rotation="50 MB",
    retention="14 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
)

# ----------------------------
# 导出配置好的 logger
# ----------------------------

__all__ = ["logger"]
