# ATRI Crawler

ATRI Crawler 通过 FastAPI 提供教务查询和寝室剩余电费查询接口。

## 环境准备

项目使用 `uv` 管理依赖。先安装 [uv](https://docs.astral.sh/uv/)，然后在仓库目录执行：

```bash
uv sync --group dev
cp .env.example .env
cp -n config/electricity_accounts.example.json config/electricity_accounts.json
```

编辑 `.env`，将 `ELECTRICITY_BASE_URL` 设置为校园支付平台根地址（不含 `/xysf`）。
例如登录页为 `http://cw-zfpt.hnucm.edu.cn/xysf/login.aspx?local=zh-cn&lx=` 时，
应填写 `ELECTRICITY_BASE_URL=http://cw-zfpt.hnucm.edu.cn`。修改配置后需重启 API 服务。
教务登录所用的 Redis 可通过 `REDIS_HOST`、`REDIS_PORT`、`REDIS_DB`、
`REDIS_PASSWORD`、`REDIS_SSL` 等环境变量配置，默认连接本机 6379 端口。

账号列表默认读取 `config/electricity_accounts.json`，格式如下：

```json
{
  "accounts": [
    {"xh": "学号", "pwd": "密码"}
  ]
}
```

示例文件中的 `123:123` 和 `1234:1234` 是占位账号，不是可用的校园账号。
正式运行前请在本地账号文件中改为已授权的账号，并限制文件访问权限。该文件已被 Git 忽略。也可通过
`ELECTRICITY_ACCOUNTS_FILE` 指向其他 JSON 文件。账号数据不会写入 API 响应或应用日志。

## 启动

```bash
uv run python main.py
```

服务默认监听 `0.0.0.0:8000`。可通过 `API_HOST`、`API_PORT` 调整；交互式文档位于
`/docs`，`/health` 用于健康检查。

Kubernetes 部署与更新见 [部署说明](deploy/k8s/README.md)。

## API

### 查询寝室剩余电费

```http
GET /api/electricity/<ROOMID>
```

调用方只需提供寝室号；服务会从内部账号池选择账号登录校园支付平台。

参数支持 4–5 位纯数字，或 `6-417`、`06-417` 这类 `x-xxx` / `xx-xxx` 格式。
四位数字或移除连字符后为四位的输入，会自动补一个前导 `0`，再作为平台的
`roomid` 字段查询。
成功响应示例：

```json
{
  "room_number": "平台返回的 ROOMID",
  "name": "6号公寓417房",
  "meter_number": "电表编号",
  "remaining_electricity": "193.17kWh",
  "balance": 119.57,
  "state": "在线",
  "category": "ElecRoomYun"
}
```

账号池按轮转顺序尝试账号。已登录 Cookie 和 CSRF 令牌仅缓存在当前进程内，默认
30 分钟过期；服务重启后缓存会清空。代码中留有 TODO，后续可分别改用 SQLite
保存账号池、Valkey 共享会话缓存。

### 教务查询

`POST /api/crawl` 保留教务查询能力。请求示例：

```json
{
  "school": "HNUCM",
  "action": "get_grades",
  "username": "学号",
  "password": "密码",
  "params": {"semester": "2023-2024-1"}
}
```

支持 `login`、`get_profile`、`get_grades` 和 `get_course_schedule` 四种操作。

## 开发工具与测试

开发依赖包含 `pytest`、`ruff` 和 `ty`。可用下列命令运行测试和静态检查：

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format .
uv run ty check
```

默认测试使用本地 mock，不访问校园服务或 Redis。真实 HNUCM 集成测试默认跳过，
需明确启用并从环境变量提供测试账号：

```bash
RUN_HNUCM_INTEGRATION_TESTS=1 \
HNUCM_ADAPTER_TEST_USERNAME=... \
HNUCM_ADAPTER_TEST_PASSWORD=... \
uv run pytest -m integration -v
```

寝室电费的真实 HTTP 接口测试会请求已启动的本地 API，并由 API 使用已配置的账号
访问校园电费平台。先在 `.env` 中配置 `ELECTRICITY_BASE_URL`，在
`config/electricity_accounts.json` 中配置可用账号，然后在一个终端启动服务：

```bash
uv run python main.py
```

在另一个终端运行（将 `ROOMID` 替换为平台实际返回的寝室标识）：

```bash
RUN_ELECTRICITY_LIVE_TESTS=1 \
ELECTRICITY_API_TEST_BASE_URL=http://127.0.0.1:8000 \
ELECTRICITY_TEST_ROOM_NUMBER=your_room_id \
uv run pytest tests/test_electricity_live_api.py -v
```

这个测试会验证 HTTP 200、返回的寝室号和非空剩余电费。普通测试运行时会跳过它。

编辑器可将 `ty server` 配置为 Python 语言服务器；项目 Python 版本在 `pyproject.toml`
中声明为 3.10 及以上。
