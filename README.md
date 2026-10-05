# ATRI Crawler

ATRI Crawler 通过 FastAPI 提供教务查询和寝室剩余电费查询接口。

## 环境准备

项目使用 `uv` 管理依赖。先安装 [uv](https://docs.astral.sh/uv/)，然后在仓库目录执行：

```bash
uv sync --group dev
cp .env.example .env
cp -n config/electricity_accounts.example.json config/electricity_accounts.json
cp -n config/academic_accounts.example.json config/academic_accounts.json
```

教务登录所用的 Redis 可通过 `REDIS_HOST`、`REDIS_PORT`、`REDIS_DB`、
`REDIS_PASSWORD`、`REDIS_SSL` 等环境变量配置，默认连接本机 6379 端口。电费
结果复用同一 Redis 配置；Redis 客户端可直接连接兼容 Redis 协议的 Valkey 服务。
连接和读写超时可用 `REDIS_SOCKET_CONNECT_TIMEOUT`、`REDIS_SOCKET_TIMEOUT` 设置。

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

教务系统使用完全独立的 `config/academic_accounts.json`；请不要将电费平台账号
复制到此文件。可通过 `ACADEMIC_ACCOUNTS_FILE` 指向其他 JSON 文件。

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
GET /api/v1/electricity/hanpu/<ROOMID>
```

调用方需指定校区和寝室号。校区使用目录接口返回的 `campus` 值；保留 `hanpu` 和 `dongtang` 作为含浦、东塘学生宿舍的兼容别名。后台采集服务会从内部账号池选择账号登录校园支付平台，且只在该校区的区域中查询，避免同号楼栋误命中。

该接口只读取当天已采集的缓存，不会因调用而请求校园财务平台。目录中不存在
该校区/寝室时返回 `404`；目录存在但尚未轮到当天采集时返回 `503`。

参数支持 4–5 位纯数字，或 `6-417`、`06-417` 这类 `x-xxx` / `xx-xxx` 格式。
四位数字或移除连字符后为四位的输入，会自动补一个前导 `0`，再作为平台的
目录标识查询，并解析学校实际的楼栋、楼层与房间 ID。国教楼同号寝室使用独立前缀：
`guojiao-07101`（也接受 `国教7-101`），与普通7号公寓的 `07101` 分别缓存和查询。
目录标签不足以确定唯一寝室时拒绝查询，不会取第一个电表。
成功响应示例：

```json
{
  "campus": "hanpu",
  "room_number": "规范化的寝室标识",
  "name": "6号公寓417房",
  "meter_number": "电表编号",
  "remaining_electricity": "193.17kWh",
  "balance": 119.57,
  "state": "在线",
  "category": "ElecRoomYun"
}
```

账号池按轮转顺序尝试账号。已登录 Cookie 和 CSRF 令牌仅缓存在当前进程内，默认
30 分钟过期；服务重启后缓存会清空。

电费平台的读数每天才刷新，因此同一校区、寝室当天的成功查询会被缓存至下一次
本地零点（默认 `Asia/Shanghai`，可由 `ELECTRICITY_CACHE_TIMEZONE` 修改）。缓存
优先写入 Redis，键前缀可通过 `ELECTRICITY_CACHE_KEY_PREFIX` 配置。若 Redis
连接、读取或写入失败，服务会立即改用当前进程内存缓存，不会因此使电费接口失败；
它会在 `REDIS_RETRY_INTERVAL_SECONDS`（默认 30 秒）后再尝试连接 Redis。进程
内存缓存不在多副本之间共享，并会在服务重启后清空。

### 获取全部有效寝室

服务启动时会自动登录一次电费平台，遍历所有可见校区、楼栋、楼层和房间，并将
不含账号、密码、Cookie 或电表读数的目录写入 `ELECTRICITY_ROOM_CATALOG_FILE`。
目录可从下面的接口读取；其中每条记录的 `campus` 可直接用于电费查询接口。

```http
GET /api/v1/electricity/rooms
```

手动刷新目录（需要内部令牌）：

```http
POST /api/v1/electricity/rooms/refresh
Authorization: Bearer <CRAWLER_INTERNAL_TOKEN>
```

财务系统每日 02:00--05:30（`Asia/Shanghai`）处于维护窗口；维护结束后的 05:30
即可将平台读数视为当日已出炉的数据。服务进程会在每周一 11:30 刷新一次目录；每天
05:30 开始按
每批 2 间、批次间隔 1 秒（120 间/分钟），收集全量寝室的当天电费读数。首次失败后
会从 06:00 起每半小时重试，避免单次上游故障导致全天没有新数据。
服务重启时也会立即同步目录；若此时已过 05:30 且当天缓存缺失或不完整，会立刻
绕过旧缓存补采全量寝室。

全量采集的进度可由内网 server 通过下列接口读取。它只返回当日已查询数量和是否已
完成，不返回寝室电费读数；当 `completed` 为 `true` 时，server 才应开始全量镜像。

```http
GET /api/v1/electricity/collection-status
```

### 探测财务系统刷新时间

配置一个读数会实际变化的测试寝室后，运行下列脚本。它每到 `:00`、`:30` 直接向
财务平台读取一次，不使用当天缓存；检测到变化时会把观测时刻和读数摘要保存到
`ELECTRICITY_UPDATE_MONITOR_STATE_FILE`，并在有足够记录时输出建议的 cron。

```bash
ELECTRICITY_UPDATE_MONITOR_CAMPUS=hanpu \
ELECTRICITY_UPDATE_MONITOR_ROOM_NUMBER=06417 \
uv run python scripts/detect_electricity_update.py
```

若由系统 cron 执行，请每半小时以 `--once` 调用一次：

```cron
*/30 * * * * cd /path/to/university_crawler && uv run python scripts/detect_electricity_update.py --once
```

半小时采样确认的当前规律是：财务系统在 02:00--05:30 维护，05:30 后当天读数出炉。
这个规律已固化在 `services/electricity_schedule.py`：维护窗口从
`FINANCIAL_SYSTEM_MAINTENANCE_START` 开始，日数据在
`FINANCIAL_SYSTEM_DAILY_DATA_AVAILABLE_AT`（05:30）可采集。结算 cron 为
`30 5 * * *`。

也可以手动刷新：

```bash
uv run python scripts/sync_electricity_rooms.py
```

### 管理电费账号池

账号池可通过以下接口管理。出于安全考虑，所有响应只返回账号 `xh`，绝不返回
密码 `pwd`。这些接口可以修改电费查询所用凭据；部署时应只在受信任的内网或管理
网关后开放。

| 操作 | 接口 | 请求体 |
| --- | --- | --- |
| 查询全部账号 | `GET /api/v1/electricity/accounts` | 无 |
| 新增账号 | `POST /api/v1/electricity/accounts` | `{"xh":"学号","pwd":"密码"}` |
| 修改账号或密码 | `PUT /api/v1/electricity/accounts/{xh}` | `{"xh":"新学号","pwd":"新密码"}`，两个字段至少提供一个 |
| 删除账号 | `DELETE /api/v1/electricity/accounts/{xh}` | 无 |

新增成功返回 `201`，删除成功返回 `204`；账号不存在返回 `404`，账号重复返回
`409`。写入采用替换式保存，读取中的电费查询不会读到半截 JSON 文件。

### 教务查询

`POST /api/v1/academic` 提供同步教务查询能力。请求示例：

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

`username` 与 `password` 现在可以同时省略。省略时，服务会从教务账号池按轮转
顺序挑选账号登录，因此调用方无需提交账号。只提供其中一个字段会返回 `422`。

### 管理教务账号池

教务账号池和电费账号池使用不同文件、不同接口：

| 操作 | 接口 | 请求体 |
| --- | --- | --- |
| 查询全部账号 | `GET /api/v1/academic/accounts` | 无 |
| 新增账号 | `POST /api/v1/academic/accounts` | `{"xh":"学号","pwd":"密码"}` |
| 修改账号或密码 | `PUT /api/v1/academic/accounts/{xh}` | `{"xh":"新学号","pwd":"新密码"}`，至少提供一个字段 |
| 删除账号 | `DELETE /api/v1/academic/accounts/{xh}` | 无 |

同样地，响应绝不包含密码；新增、更新、删除分别返回 `201`、`200`、`204`。教务
账号池可影响不带账号的教务查询结果，管理接口应只向可信内网或管理网关开放。

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
访问校园电费平台。在 `config/electricity_accounts.json` 中配置可用账号，然后在一个
终端启动服务：

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

电费相关接口（寝室读数、目录、目录刷新、采集状态、账号池管理）均支持
`school=HNUCM` 查询参数，与教务接口使用相同的学校代码。目前仅支持 `HNUCM`，
未支持的学校返回 422；旧请求省略时默认 HNUCM。服务实例按学校缓存并通过
`ELECTRICITY_SERVICES` 注册表选择，账号池也从对应学校的服务实例获取。

## 学校与校区隔离

`school` 表示学校（当前 `HNUCM`），`campus` 表示该学校内的校区；两者独立。
教务查询及电费、教务账号池管理接口使用相同的学校代码，未实现的学校返回 422。
账号池管理均支持 `?school=HNUCM`，POST/PUT 可在请求体显式提供同一个 `school`；
请求体与查询参数不一致时拒绝请求。响应返回学校字段，但始终不返回密码。

两个账号池文件使用如下记录结构；旧记录省略 school 时按 HNUCM 读取，下一次
账号写入时补齐字段，不会丢失已有账号。账号主键为 `(school, xh)`：

```json
{"accounts": [{"school": "HNUCM", "xh": "学号", "pwd": "私密凭据"}]}
```

教务账号池按 school 选择并独立轮转；来自另一学校的同名账号不会参与登录。
电费读数 Redis 键包含 school，登录 Cookie 缓存键也包含学校身份。非默认学校的
寝室目录和采集状态文件在配置文件名中追加 `.学校代码`，文件内容也记录 school；
旧 HNUCM 文件保持原路径并继续可读。升级后旧的无 school 电费缓存键不再使用，
启动补采会重建当天缓存。

目前只实现 HNUCM。新增学校时须同时扩展本项目 `schemas/school.py`、对应教务/
电费适配器注册表，以及 server `app/domain/schools.py`，实现该学校的校区规则。
仅在账号文件中添加新学校代码不会自动开放查询能力。

## 测试与回归

完整测试矩阵、保留/新增用例和并发测量入口见 [测试方案](docs/testing-plan.md)。
