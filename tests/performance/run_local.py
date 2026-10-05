"""运行真实 localhost HTTP 并发场景，依赖使用测试配置。"""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

from load_http import run

ROOT = Path(__file__).resolve().parents[2]


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = dict(
        os.environ, PYTHONPATH=str(ROOT) + os.pathsep + str(Path(__file__).parent)
    )
    output = ROOT / "tests/performance/results"
    output.mkdir(exist_ok=True)
    with (output / "service.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "fixture_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--lifespan",
                "off",
                "--no-access-log",
            ],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            origin = f"http://127.0.0.1:{port}"
            for _ in range(200):
                if process.poll() is not None:
                    message = "测试服务启动失败，查看 results/service.log"
                    raise RuntimeError(message)
                try:
                    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        origin + "/openapi.json", timeout=0.2
                    ).close()
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise TimeoutError("测试服务启动超时")
            # 从子进程配置读取场景，避免在压测生成器进程加载机器人。
            scenarios = [
                ("liveness", "/health", 200, {}),
                (
                    "invalid-token",
                    "/api/v1/academic/accounts",
                    401,
                    {"Authorization": "Bearer invalid-test"},
                ),
            ]
            scenarios.append(
                (
                    "account-pool-read",
                    "/api/v1/academic/accounts",
                    200,
                    {"Authorization": "Bearer load-test-token"},
                )
            )
            reports = []
            for name, path, status, headers in scenarios:
                run(
                    origin + path,
                    users=1,
                    requests=10,
                    timeout=5,
                    expected_status=status,
                    headers=headers,
                )
                for users in [10, 50, 100]:
                    report = run(
                        origin + path,
                        users=users,
                        requests=300,
                        timeout=5,
                        expected_status=status,
                        headers=headers,
                        pid=process.pid,
                    )
                    report["scenario"] = name
                    report["passed"] = (
                        report["error_rate"] == 0 and report["latency_ms"]["p95"] < 2000
                    )
                    reports.append(report)
            (output / "local.json").write_text(
                json.dumps(reports, ensure_ascii=False, indent=2) + "\n"
            )
            for report in reports:
                sys.stdout.write(
                    f"{report['scenario']} users={report['users']} "
                    f"QPS={report['qps']:.1f} "
                    f"p95={report['latency_ms']['p95']:.1f}ms "
                    f"errors={report['error_rate']:.2%}\n"
                )
            return 0 if all(report["passed"] for report in reports) else 1
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
