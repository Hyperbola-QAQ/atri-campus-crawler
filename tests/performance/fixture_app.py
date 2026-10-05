"""临时账号池和真实路由，不启动后台采集。"""

import os
import tempfile
from importlib import import_module
from pathlib import Path

_temp = tempfile.TemporaryDirectory(prefix="crawler-load-")
accounts = Path(_temp.name) / "accounts.json"
accounts.write_text('{"accounts": []}', encoding="utf-8")
os.environ["CRAWLER_INTERNAL_TOKEN"] = "load-test-token"
os.environ["ACADEMIC_ACCOUNTS_FILE"] = str(accounts)
app = import_module("main").app
