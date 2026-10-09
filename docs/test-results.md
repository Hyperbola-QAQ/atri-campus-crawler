# 2026-10-08 crawler 覆盖率与 CI 门槛验证

执行 `bash scripts/test-regression.sh -q`：**431 passed、12 skipped**。行覆盖率 **99.26%（1866/1880）**，分支覆盖率 **96.89%（467/482）**，综合覆盖率 **98.77%**；三项均达到 95% 门槛。统计范围仍为 `adapter`、`main`、`services`、`utils`、`schemas`，未缩小范围或新增业务排除规则。

新增 13 个账号校验、学校隔离、寝室身份和旧目录兼容用例，以及 5 个独立覆盖率门槛用例。CI 改为调用同一本地回归脚本，实际收集 JSON/XML 覆盖率并独立验证行与分支均达到 95%。`ruff check .` 通过。现有 12 个真实教务、Redis 和电费服务测试仍为 opt-in，本次未验收真实平台。保留既有 lxml FutureWarning。

---

# 2026-10-07 crawler 覆盖率补测记录

执行 `bash scripts/test-regression.sh -q --cov-report=json:/tmp/crawler-coverage-final.json`：**413 passed、12 skipped**，语句/分支综合覆盖率 **98.22%**。统计范围为 `adapter`、`main`、`services`、`utils`、`schemas`，未新增覆盖率排除规则，回归门槛由 68% 提高至 95%。

新增 242 个离线参数化用例，覆盖教务认证及适配器返回、HTML/Excel 异常输入、HTTP 错误映射、启动任务清理、电费门户协议、账号文件及目录持久化、断点采集和缓存降级。普通测试模拟 Redis 不可用，避免访问开发机真实 Redis；原有 12 个真实服务用例保持 opt-in。新增测试及 conftest 的 Ruff 检查通过。

测试触发了既有成绩解析代码中 lxml 元素布尔判断的 1 条 FutureWarning；不影响通过结果。真实教务/财务平台和 Redis 集成验收本次未执行。

---

# 2026-10-05 测试执行记录

本次在 Linux 7.2.8-zen1-2-zen、32 个逻辑CPU的开发机执行。Python服务为本地单实例Uvicorn，Web为Next.js生产构建。所有测试数据隔离；高并发场景为短时基线，不代表生产持续容量。

| 项目 | 最终回归 | 覆盖率范围与结果 |
|---|---|---|
| server | 260 passed | 86.06%，整个 app 语句/分支综合 |
| university_crawler | 171 passed，12 个真实服务用例默认跳过 | 69.94%，adapter/main/services/utils 语句/分支综合 |
| robo | 463 passed | 74.34%，全 src/plugins 语句/分支综合 |
| web | 33 单元 + 13 HTTP + 14 Chromium；类型/lint/构建/冒烟通过 | 已导入7模块：行100%、分支100%、函数96.88% |

## 本地并发实测

每行300请求。users为同时启动的工作线程数量，peak为实际在途峰值；通过启动屏障避免顺序启动造成并发不足。401/403拒绝场景单列。CPU为服务进程消耗CPU秒/测试墙钟秒×100（可能超过100%），RSS为50ms采样峰值，不含子进程；短场景只能作为基线。

| 项目/场景 | users / peak | QPS | p95 ms | p99 ms | 错误率 | CPU % | RSS MiB | 资源样本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| server/liveness | 10 / 10 | 5001.3 | 2.5 | 4.6 | 0% | 100.0 | 135.9 | 2 |
| server/liveness | 50 / 50 | 4388.2 | 13.2 | 14.4 | 0% | 87.8 | 136.2 | 2 |
| server/liveness | 100 / 100 | 4222.1 | 20.1 | 20.7 | 0% | 70.4 | 136.2 | 2 |
| server/invalid-session | 10 / 10 | 1936.3 | 7.9 | 11.6 | 0% | 96.8 | 136.9 | 4 |
| server/invalid-session | 50 / 50 | 1882.9 | 31.0 | 37.0 | 0% | 94.1 | 138.4 | 4 |
| server/invalid-session | 100 / 100 | 1969.0 | 51.0 | 51.9 | 0% | 91.9 | 139.7 | 4 |
| server/authenticated-db-read | 10 / 10 | 573.8 | 21.2 | 107.3 | 0% | 107.1 | 144.4 | 11 |
| server/authenticated-db-read | 50 / 50 | 750.0 | 78.2 | 90.7 | 0% | 112.5 | 150.4 | 8 |
| server/authenticated-db-read | 100 / 100 | 688.0 | 173.6 | 176.7 | 0% | 110.1 | 152.4 | 9 |
| university_crawler/liveness | 10 / 10 | 5730.9 | 3.0 | 4.7 | 0% | 114.6 | 149.5 | 2 |
| university_crawler/liveness | 50 / 50 | 4567.8 | 10.3 | 10.4 | 0% | 60.9 | 149.8 | 2 |
| university_crawler/liveness | 100 / 100 | 4169.2 | 24.4 | 31.3 | 0% | 55.6 | 149.8 | 2 |
| university_crawler/invalid-token | 10 / 10 | 7089.6 | 2.3 | 3.3 | 0% | 70.9 | 149.8 | 1 |
| university_crawler/invalid-token | 50 / 50 | 6997.5 | 10.8 | 13.0 | 0% | 46.6 | 149.8 | 1 |
| university_crawler/invalid-token | 100 / 100 | 5585.7 | 24.8 | 28.3 | 0% | 55.9 | 149.8 | 2 |
| university_crawler/account-pool-read | 10 / 10 | 4181.7 | 3.1 | 4.3 | 0% | 83.6 | 149.8 | 2 |
| university_crawler/account-pool-read | 50 / 50 | 3867.1 | 12.5 | 12.7 | 0% | 90.2 | 149.8 | 2 |
| university_crawler/account-pool-read | 100 / 100 | 4075.0 | 23.4 | 23.5 | 0% | 95.1 | 150.8 | 2 |
| robo/management-read | 10 / 10 | 2803.9 | 4.8 | 5.5 | 0% | 84.1 | 157.0 | 3 |
| robo/management-read | 50 / 50 | 3063.8 | 17.4 | 19.2 | 0% | 91.9 | 157.0 | 2 |
| robo/management-read | 100 / 100 | 3060.8 | 32.6 | 32.7 | 0% | 81.6 | 157.0 | 2 |
| robo/invalid-token | 10 / 10 | 6420.0 | 2.9 | 4.0 | 0% | 107.0 | 157.0 | 1 |
| robo/invalid-token | 50 / 50 | 5230.1 | 14.3 | 18.1 | 0% | 52.3 | 157.0 | 2 |
| robo/invalid-token | 100 / 100 | 4678.7 | 34.4 | 40.4 | 0% | 46.8 | 157.2 | 2 |
| web/management-read | 10 / 10 | 693.3 | 21.4 | 30.2 | 0% | 136.3 | 219.8 | 9 |
| web/management-read | 50 / 50 | 980.1 | 54.1 | 55.4 | 0% | 137.2 | 257.7 | 7 |
| web/management-read | 100 / 100 | 1160.1 | 86.2 | 99.8 | 0% | 112.1 | 274.0 | 6 |
| web/anonymous-denied | 10 / 10 | 2158.3 | 6.8 | 7.2 | 0% | 107.9 | 281.7 | 3 |
| web/anonymous-denied | 50 / 50 | 1982.9 | 27.0 | 27.6 | 0% | 112.4 | 291.7 | 4 |
| web/anonymous-denied | 100 / 100 | 1970.1 | 50.9 | 58.4 | 0% | 105.1 | 300.0 | 4 |

## 测试发现并修复的问题

- Server：100并发真实认证读取可复现线程池/数据库连接相互等待，约80%超时。鉴权改为短生命周期会话，返回身份前释放连接；写接口显式将身份加入自身会话。补上鉴权连接释放断言，保留并通过跨Web/机器人绑定和账户修改回归。修复后同场景错误率为0。
- crawler：safe_float 对数字/列表/字典抛AttributeError，NaN/Infinity未过滤；safe_int对Infinity抛OverflowError。保留百分数及整数截断规则，异常和非有限值返回默认值。
- robo：扩展响应顶层或data字段类型异常导致AttributeError，True被当作等级1。限制对象结构，并排除布尔等级；超时不自动重试、取消继续传播。
- Web：application/json类型但正文无法解析时仍返回200。非法正文现在返回502；真实backend超时、空响应和409响应回归通过。

## 可重复运行与限制

命令和全量保留清单见各项目 docs/testing-plan.md。原始JSON位于各项目 tests/performance/results/local.json（Git忽略），本文件保存本次测量快照。所有30个场景/并发组合均达到错误率0、p95<2000ms门槛。

首次压测受HTTP代理影响，已让localhost自动直连，表中只含重跑结果。开发沙箱会使部分异步线程唤醒/退出阻塞，最终pytest、生产构建、HTTP/浏览器与压测均在允许启动本地进程/网络的环境执行。

爬虫12个真实教务/Redis/电费用例未启用，因此没有声称真实平台验收通过。PostgreSQL、Kubernetes和真实QQ链路、持续压测、内存泄漏和外部平台限流仍需按方案在专用测试部署验证；当前报告不覆盖其容量。

## 2026-10-08 系统修复三轮验证

- 第一轮：启动断点恢复与缺失补采统一；绑定/登录强制验证密码；Secret 首次初始化到可写 PVC，由 API 管理；账号文件原子写入并保留 0600 权限；缓存容量及 Redis 连接释放。全套 455 passed、12 skipped。
- 第二轮：跨项目契约检查发现无 checkpoint 的首次补采不会发布 completed，修复为补采完成发布全目录状态；周期任务等待启动恢复，避免 05:30 边界重复采集；日缓存清扫避免每个寝室遍历全表。全套 458 passed、12 skipped。
- 第三轮：使用实际 ASGI 与 HNUCMAdapter 验证撤销旧密码即使有缓存仍被绑定验证拒绝；验证初始化发布失败及竞争写入不覆盖账号池、无临时文件残留。最终全套 461 passed、12 skipped，Ruff、git diff --check、部署脚本 shell 语法及变更 YAML 解析通过。

执行方式 `.venv/bin/python -m pytest -q`。新增回归见 `tests/test_system_remediation.py`。
这三轮只覆盖离线与本地契约，没有部署或访问真实学校、Redis、Kubernetes 服务。
