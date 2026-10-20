"""仅首次初始化可写账户池，后续部署保留 API 修改。"""

import os
import tempfile
from pathlib import Path

from services.electricity import ElectricityAccountPool


def seed_account_pool(source: Path, destination: Path) -> bool:
    """校验源账户池后，仅在目标不存在时原子初始化；已有 API 修改不会被部署覆盖。
    同目录临时文件 fsync 后以硬链接竞争创建，失败或竞争失败只清理本次临时文件。
    创建成功返回 True，已有目标返回 False；文件系统与校验错误向上传播。
    """
    if destination.is_file():
        return False
    # 初始配置无效时让初始化容器失败，不能打印配置内容。
    ElectricityAccountPool(source)._read_records()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as output:
            temporary_path = Path(output.name)
            output.write(source.read_bytes())
            output.flush()
            os.fsync(output.fileno())
        # 仅在不存在时原子创建，避免中断留下不完整 JSON，也避免并发初始化覆盖 API 管理的账户池。
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            return False
        return True
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    seed_account_pool(
        Path(os.environ["ELECTRICITY_ACCOUNTS_SEED_FILE"]),
        Path(os.environ["ELECTRICITY_ACCOUNTS_FILE"]),
    )
