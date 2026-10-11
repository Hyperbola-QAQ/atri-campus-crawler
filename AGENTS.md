# 项目 Agent 约定

## 生产发布与 CI/CD

- 生产发布由 GitHub Actions CI/CD 执行。提交、推送前必须检查 `.github/workflows/` 的触发条件，推送后使用 `gh run list`、`gh run view <run-id> --json jobs` 核实对应提交的 CI 与 deploy job；失败时用 `gh run view <run-id> --log-failed` 排查。
- 不把“没有手动运行部署脚本”表述为“未发布生产”：推送 main 可能自动触发生产部署。只有对应提交的 deploy job 成功并通过工作流 rollout/readiness 校验后，才能报告发布成功；区分失败、跳过和仍在运行。
- 常规发布走工作流，不在本地或 SSH 中直接执行部署脚本。工作流内部调用部署脚本属于正常 CI/CD。手动发布请求使用工作流已有的 workflow_dispatch 入口。
- 部署开关与 runner 配置以实时 `gh variable list -R <owner/repo>` 和工作流为准；不要把本文记录的开关状态视为永久值。不得输出 secrets。

- 仓库：Hyperbola-QAQ/atri-campus-crawler。CI 执行 Ruff 和离线回归/覆盖率门禁；deploy 依赖 ci 成功。main 推送在 ENABLE_PRODUCTION_DEPLOY=true 且 ENABLE_AUTO_DEPLOY=true 时自动部署；main 的 workflow_dispatch 在生产开关开启时部署。自托管 runner 在 production 环境调用 deploy/k8s/deploy.sh，保留运行时 secrets 并等待 rollout。
- 2026-10-09 核实：生产与自动部署开关均为 true。
