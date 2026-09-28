#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
k8s_ssh_target="${K8S_SSH_TARGET:-hyperbola@192.168.86.11}"
ssh_config="${SSH_CONFIG:-/dev/null}"
release_id="${RELEASE_ID:-$(date -u +%Y%m%d%H%M%S%N)-$(git -C "$repo_root" rev-parse --short=8 HEAD)}"

if [[ ! "$release_id" =~ ^[a-z0-9][a-z0-9._-]{0,62}$ ]]; then
  echo "RELEASE_ID 格式无效。" >&2
  exit 2
fi

temporary_dir="$(mktemp -d /tmp/crawler-deploy.XXXXXX)"
trap 'rm -rf "$temporary_dir"' EXIT
python3 - "$repo_root/deploy/k8s/crawler.yaml" "$release_id" "$temporary_dir/crawler.yaml" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
needle = "__CRAWLER_RELEASE__"
if source.count(needle) != 3:
    raise SystemExit("Expected exactly three release placeholders")
Path(sys.argv[3]).write_text(source.replace(needle, sys.argv[2]), encoding="utf-8")
PY

ssh_options=(-F "$ssh_config" -o BatchMode=yes -o ConnectTimeout=10)
remote=(ssh "${ssh_options[@]}" "$k8s_ssh_target")

echo "检查集群访问"
"${remote[@]}" kubectl get nodes --no-headers > /dev/null

echo "创建部署资源与配置"
"${remote[@]}" kubectl apply -f - < "$repo_root/deploy/k8s/crawler-base.yaml"

python3 - "$repo_root/.env" "$repo_root/config/electricity_accounts.json" <<'PY' \
  | "${remote[@]}" kubectl apply -f - > /dev/null
import json
from pathlib import Path
import sys

env_file = Path(sys.argv[1])
accounts_file = Path(sys.argv[2])
if not env_file.is_file() or not accounts_file.is_file():
    raise SystemExit("缺少 .env 或 config/electricity_accounts.json")
values = {}
for line in env_file.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"\'')
base_url = values.get("ELECTRICITY_BASE_URL", "")
if not base_url.startswith(("http://", "https://")):
    raise SystemExit(".env 中 ELECTRICITY_BASE_URL 必须是 HTTP(S) 地址")
raw_accounts = accounts_file.read_text(encoding="utf-8")
accounts = json.loads(raw_accounts)
if not isinstance(accounts, dict) or not isinstance(accounts.get("accounts"), list) or not accounts["accounts"]:
    raise SystemExit("电费账号池为空或格式错误")
for index, (name, data) in enumerate((
    ("crawler-runtime-secrets", {"ELECTRICITY_BASE_URL": base_url}),
    ("crawler-electricity-accounts", {"accounts.json": raw_accounts}),
)):
    if index:
        print("---")
    print(json.dumps({
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": name, "namespace": "university-crawler"},
        "stringData": data,
    }))
PY

# 从集群现有 Secret 复制密码，避免密码出现在本机输出或命令参数里。
"${remote[@]}" python3 - <<'PY'
import json
import subprocess

source = json.loads(subprocess.check_output([
    "kubectl", "-n", "databases", "get", "secret", "valkey-authentication", "-o", "json"
]))
password = source["data"]["password"]
destination = {
    "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
    "metadata": {"name": "crawler-redis-auth", "namespace": "university-crawler"},
    "data": {"REDIS_PASSWORD": password},
}
subprocess.run(["kubectl", "apply", "-f", "-"],
               input=json.dumps(destination).encode(), check=True, stdout=subprocess.DEVNULL)
PY

echo "上传源码版本：$release_id"
"${remote[@]}" kubectl -n university-crawler delete pod crawler-state-importer --ignore-not-found --wait=true > /dev/null
"${remote[@]}" kubectl apply -f - < "$repo_root/deploy/k8s/state-importer.yaml"
"${remote[@]}" kubectl -n university-crawler wait --for=condition=Ready pod/crawler-state-importer --timeout=240s

release_dir="/var/lib/crawler/releases/$release_id"
if "${remote[@]}" kubectl -n university-crawler exec crawler-state-importer -- test -f "$release_dir/.ready" 2> /dev/null; then
  echo "版本 $release_id 已存在，请使用新的 RELEASE_ID。" >&2
  exit 2
fi
"${remote[@]}" kubectl -n university-crawler exec crawler-state-importer -- mkdir -p "$release_dir"
tar -czf - -C "$repo_root" main.py pyproject.toml uv.lock README.md LICENSE adapter schemas services utils \
  | "${remote[@]}" kubectl -n university-crawler exec -i crawler-state-importer -- tar -xzf - -C "$release_dir"
"${remote[@]}" kubectl -n university-crawler exec crawler-state-importer -- touch "$release_dir/.ready"
"${remote[@]}" kubectl -n university-crawler delete pod crawler-state-importer --wait=true > /dev/null

echo "切换 API 到新版本"
"${remote[@]}" kubectl apply -f - < "$temporary_dir/crawler.yaml"
"${remote[@]}" kubectl -n university-crawler rollout status deployment/crawler --timeout=600s
"${remote[@]}" kubectl -n university-crawler get deployment crawler -o wide
echo "部署完成：$release_id"
