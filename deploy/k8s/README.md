# Kubernetes 部署

本服务参照 `robo` 项目，使用 uv 官方镜像和 NFS PVC 保存源码版本、虚拟环境及依赖缓存。部署脚本通过 SSH 连接集群控制平面，不需要构建或推送镜像。

在项目根目录准备 `config/electricity_accounts.json`，然后运行：

```bash
bash deploy/k8s/deploy.sh
```

默认 SSH 目标为 `hyperbola@192.168.86.11`，可用 `K8S_SSH_TARGET` 和 `SSH_CONFIG` 调整。集群节点首次运行需要拉取 `ghcr.io/astral-sh/uv:python3.14-trixie-slim` 并访问 PyPI 下载依赖。

部署在 `atri-campus-crawler` 命名空间，Service 为 `university-crawler:8000`，集群内访问地址为 `http://university-crawler.atri-campus-crawler.svc.cluster.local:8000`。公网电费接口为 `https://university-crawler.hyperbola.cc/api/v1/electricity/...`。`/health` 用于存活和就绪检查。

每次部署生成独立源码版本目录并更新 Deployment。回退可运行：

```bash
kubectl -n atri-campus-crawler rollout undo deployment/university-crawler
```

旧版本保留在 `crawler-state` PVC 中。当前仅运行一个副本，因为电费账号轮转与 Cookie 缓存在进程内。更新时 API 会短暂中断。

教务账号池通过 `ACADEMIC_ACCOUNTS_FILE` 固定保存在 PVC 的
`/var/lib/crawler/config/academic_accounts.json`，账号接口写入的数据会跨源码版本保留。

电费 Secret 只作为首次初始化来源。init container 将有效配置原子初始化到
PVC 的 `/var/lib/crawler/config/electricity_accounts.json`（文件权限 0600）。
此后电费账号接口管理该文件，更新 Secret 或重新部署不会覆盖 API 已修改的账号。
需要变更账号时使用账号 API；如确需重置为 Secret 配置，应先备份 PVC 文件并在
服务停止后由运维删除该文件，再启动服务。不要通过反复部署覆盖运行中的账号池。
