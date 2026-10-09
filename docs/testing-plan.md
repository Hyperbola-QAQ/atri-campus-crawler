# university_crawler 测试方案与回归清单

本方案更新于 2026-10-08。执行结果与测量快照见 [本次测试记录](test-results.md)。所有已有测试必须保留；补测与修复一起提交。测试失败返回非零，不以跳过新增用例、自动重试或降低既有门槛掩盖失败。

| 类型 | 用例位置 | 必验行为 |
|---|---|---|
| 单元 | tests/test_adapter_*.py、test_electricity*.py、test_numeric_paths.py、test_cookie_cache_paths.py | 课表/成绩/档案解析、寝室规范化、空值、异常数值、缓存到期和拷贝隔离 |
| 接口 | tests/test_main.py、test_school_isolation.py、test_api_contract_paths.py | 正常 CRUD、鉴权失败、无令牌配置、缺参/null/空串、学校隔离、重复删除 |
| 路径 | tests/test_electricity.py、test_electricity_schedule.py、test_schedule_paths.py | 账号轮转、缓存命中/失效、全部账号失败、调度异常后继续、精确时间边界与取消传播 |
| 新增回归 | tests/test_api_contract_paths.py、test_cookie_cache_paths.py、test_numeric_paths.py、test_schedule_paths.py | 30 个同账号并发创建仅一个成功、100 用户缓存隔离、NaN/Infinity 安全转换、循环重试 |

## 执行与隔离

```sh
uv sync --group dev
bash scripts/test-regression.sh -q
.venv/bin/python tests/performance/run_local.py
```

pytest 全量执行包含新增的压测工具单元测试；load 脚本单独启动 localhost Uvicorn，并关闭 lifespan，避免真实后台采集/群推送。
默认不运行真实教务、Redis 和电费服务测试；保留 12 个 opt-in 用例。仅在独立验收环境通过 RUN_HNUCM_INTEGRATION_TESTS、RUN_INFRA_INTEGRATION_TESTS、RUN_ELECTRICITY_LIVE_TESTS 启用，并从环境提供测试账号。覆盖率统计含 adapter/main/services/utils/schemas，启用分支覆盖；语句/分支综合覆盖率、行覆盖率和分支覆盖率分别以 95% 为回归门槛，不排除未覆盖的业务代码。普通测试模拟 Redis 不可用以验证内存降级，避免依赖开发机 Redis 和网络重试；显式注入的缓存模拟与 opt-in 集成测试不受影响。真实平台仍须现场验收。

本地脚本与 CI 共用 `scripts/test-regression.sh`，生成 `coverage.json`、`coverage.xml`，随后由 `scripts/check-coverage.py` 独立检查行与分支比例；综合分数达标不能掩盖分支不足。`tests/test_coverage_gate.py` 验证 94% 拒绝、95% 接受及独立指标门槛。

## 并发与容量验证

本地场景固定 10、50、100 个闭环用户，每场景每档 300 个 GET 请求，Python 场景先做 10 次预热；无自动重试，完整读取响应正文。正常请求必须返回 200，鉴权拒绝场景以预期 401/403 为正确结果，拒绝吞吐不能解释成正常业务容量。默认错误率必须为 0，p95 必须小于 2000ms；部署验收可以设置更严格的 QPS 下限。

产物 tests/performance/results/local.json 记录实际 QPS、p50/p95/p99/max、响应状态分布、错误率、服务进程 CPU（单核100%口径）与实际在途请求峰值、采样峰值 RSS、生成器 RSS。资源来自 /proc/服务PID，未提供PID或读取失败显示 null；不把生成器资源当作服务资源。RSS 每50ms采样，短场景样本很少，不能证明长时间内存稳定；不含子进程资源。localhost 自动绕过系统代理。

独立环境可用以下通用入口，LOAD_TOKEN / LOAD_COOKIE 从环境传入，不把凭据写在命令参数中：

```sh
python3 tests/performance/load_http.py http://127.0.0.1:8000/health --users 100 --requests 10000 --timeout 5 --pid 12345 --max-error-rate 0 --max-p95-ms 500 --min-qps 100 --output /tmp/load-result.json
```

URL、端口和PID需替换为实际测试部署；先测健康，再测有身份的读路径。长期验收依次使用10/50/100/200用户，每档至少60秒（增加 requests 达到所需时长），重复3次；使用独立压测机并记录CPU/内存/实例数/数据库池大小。另做突发200用户、稳定100用户15分钟和下游故障恢复；服务崩溃、超时、错误率越界、内存持续增长均为失败。写入竞争由上述接口用例验证，若部署要持续写压测，应使用专用账号与可清理数据集。SQLite与模拟后端结果不代表PostgreSQL、学校财务系统、Redis、Kubernetes或真实QQ的容量。

## 回归保留清单

以下现有文件及本次新增文件全部纳入回归；不得只运行新增目录。每次修复至少跑所属项目全量测试；鉴权/身份/数据协议变动还需跑跨项目 HTTP 与命令回归。覆盖率报告保留缺失行和分支，后续功能修改时必须补上关联路径。

- `tests/performance/test_load_http.py`
- `tests/test_validation_boundaries.py`
- `tests/test_coverage_gate.py`
- `tests/test_auth_unit.py`
- `tests/test_academic_unit.py`
- `tests/test_api_failure_paths.py`
- `tests/test_electricity_protocol_edges.py`
- `tests/test_electricity_service_edges.py`
- `tests/test_academic_tempfiles.py`
- `tests/test_adapter_course_schedule.py`
- `tests/test_adapter_grade.py`
- `tests/test_adapter_login.py`
- `tests/test_adapter_profile.py`
- `tests/test_api_contract_paths.py`
- `tests/test_cookie_cache_paths.py`
- `tests/test_electricity.py`
- `tests/test_electricity_live_api.py`
- `tests/test_electricity_room_identity.py`
- `tests/test_electricity_schedule.py`
- `tests/test_electricity_settlement.py`
- `tests/test_electricity_update_monitor.py`
- `tests/test_main.py`
- `tests/test_numeric_paths.py`
- `tests/test_ocr.py`
- `tests/test_redis.py`
- `tests/test_schedule_paths.py`
- `tests/test_school_isolation.py`
- `tests/test_system_remediation.py`：验证真实登录证明、启动拥有者与调度等待、补采完成状态、Secret 首次初始化/API 配置归属、原子文件失败与竞争、缓存容量和 Redis 生命周期。
