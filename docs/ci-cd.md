# GitHub Actions CI/CD

`.github/workflows/ci-cd.yml` 在 main 的 push、PR 和手动执行时运行锁定依赖安装、Ruff 检查与格式检查、ty 和离线测试。CI 使用 GitHub 托管 runner，PR 不运行生产部署。测试失败时阻止部署，并上传可用的测试报告。

部署复用 Mizuki 的 `[self-hosted, linux, atri-deploy]` runner 和现有 Kubernetes SSH 发布脚本。runner 必须对本仓库可用，并具有 Bash、Python 3、SSH、tar，以及到集群的网络访问和受信任的 SSH host key。SSH 账户需要现有脚本所用的 kubectl 权限。

先在本地完成一次初始化部署。CI 设置 `PRESERVE_RUNTIME_SECRETS=true`，复用集群已有业务密钥，不从 runner 读取 `.env` 或电费账户文件；缺少必要 Secret 时发布失败。轮换密钥或账号池仍通过本地发布脚本完成。

配置 production environment；建议添加部署审批。仓库变量 `K8S_SSH_TARGET` 默认为 `hyperbola@192.168.86.11`，`SSH_CONFIG` 默认为 `/dev/null`，可指向 runner 上预配置的 SSH 配置文件。不要将私钥提交到仓库。

每个仓库需要先注册生产 runner（Mizuki 的仓库级 runner 无法跨仓库使用），并设置 `ENABLE_PRODUCTION_DEPLOY=true` 才会调度部署；未配置时 CD 跳过，不会无限等待 runner。`DEPLOY_RUNNER_LABELS` 可覆盖 runner 标签，值是 JSON 数组，例如 `["self-hosted","linux","atri-deploy"]`。启用后默认只允许 main 上手动 Run workflow 发布。将仓库变量 `ENABLE_AUTO_DEPLOY` 设置为 `true` 后，main push 在 CI 成功后自动部署。部署串行，版本编号包含 Actions run ID 和 attempt，重跑不会覆盖已有版本。不要给不可信 PR 或公共仓库开放生产 runner。

server 需 crawler 已经初始化；更新顺序为 crawler、server、调用端。server 发布前应备份数据库，启动执行 Alembic 迁移。回退源码不会回退数据库，数据库恢复遵循部署说明。

现有 type checking 存在历史诊断，因此 ty 步骤先作为非阻断检查保留可见结果。crawler 现有 7 个文件存在格式差异，format 步骤同样非阻断。Ruff lint 和离线测试仍阻断发布；修复历史问题后应去掉相应 `continue-on-error`。
