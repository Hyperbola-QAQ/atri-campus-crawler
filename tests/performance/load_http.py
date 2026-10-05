"""HTTP 闭环压测：固定并发、有限请求数、独立服务进程资源采样。"""

import argparse
import concurrent.futures
import json
import math
import os
import resource
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def percentile(values, percent):
    """最近秩百分位，空样本不可被误认为零延迟。"""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]


def process_sample(pid):
    """读取 Linux 服务进程累计 CPU 秒数与当前 RSS，不含其子进程。"""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {
            "cpu_seconds": (int(fields[11]) + int(fields[12]))
            / os.sysconf("SC_CLK_TCK"),
            "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
        }
    except (OSError, ValueError, IndexError):
        return None


def _worker(
    count,
    *,
    url,
    headers,
    timeout,
    http_open,
    lock,
    latencies,
    statuses,
    barrier,
    in_flight,
):
    barrier.wait()
    for _ in range(count):
        with lock:
            in_flight["current"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
        began = time.perf_counter()
        status = "transport_error"
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with http_open(request, timeout=timeout) as response:
                response.read()
                status = str(response.status)
        except urllib.error.HTTPError as error:
            error.read()
            error.close()
            status = str(error.code)
        except (OSError, urllib.error.URLError, TimeoutError):
            pass
        elapsed = (time.perf_counter() - began) * 1000
        with lock:
            in_flight["current"] -= 1
            latencies.append(elapsed)
            statuses[status] = statuses.get(status, 0) + 1


def run(url, *, users, requests, timeout, expected_status, headers=None, pid=None):
    if users < 1 or requests < users or timeout <= 0:
        message = "users >= 1, requests >= users, timeout > 0"
        raise ValueError(message)
    barrier = threading.Barrier(users)
    in_flight = {"current": 0, "peak": 0}
    http_open = urllib.request.urlopen
    if urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"}:
        http_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    latencies, statuses = [], {}
    lock = threading.Lock()
    resource_samples = []
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            value = process_sample(pid)
            if value is not None:
                resource_samples.append(value)
            stop.wait(0.05)

    sampler = threading.Thread(target=sample, daemon=True) if pid else None
    before = process_sample(pid) if pid else None
    if sampler:
        sampler.start()
    start = time.perf_counter()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=users) as pool:
            futures = [
                pool.submit(
                    _worker,
                    requests // users + (i < requests % users),
                    url=url,
                    headers=headers,
                    timeout=timeout,
                    http_open=http_open,
                    lock=lock,
                    latencies=latencies,
                    statuses=statuses,
                    barrier=barrier,
                    in_flight=in_flight,
                )
                for i in range(users)
            ]
            for future in futures:
                future.result()
    finally:
        stop.set()
        if sampler:
            sampler.join()
    duration = time.perf_counter() - start
    after = process_sample(pid) if pid else None
    errors = requests - statuses.get(str(expected_status), 0)
    return {
        "url": url.split("?", 1)[0],
        "users": users,
        "peak_in_flight": in_flight["peak"],
        "requests": requests,
        "completed": len(latencies),
        "duration_seconds": duration,
        "qps": len(latencies) / duration,
        "error_rate": errors / requests,
        "statuses": statuses,
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": max(latencies),
        },
        "generator_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "target": {
            "pid": pid,
            "samples": len(resource_samples),
            "peak_rss_bytes": max(
                (s["rss_bytes"] for s in resource_samples), default=None
            ),
            "cpu_percent_one_core": 100
            * (after["cpu_seconds"] - before["cpu_seconds"])
            / duration
            if before and after
            else None,
        },
        "model": "closed-loop, HTTP GET, no retry, response body consumed",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--expected-status", type=int, default=200)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--max-error-rate", type=float, default=0)
    parser.add_argument("--max-p95-ms", type=float, default=2000)
    parser.add_argument("--min-qps", type=float, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.users < 1 or args.requests < args.users or args.timeout <= 0:
        parser.error("users >= 1, requests >= users, timeout > 0")
    headers = {}
    if os.getenv("LOAD_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["LOAD_TOKEN"]
    if os.getenv("LOAD_COOKIE"):
        headers["Cookie"] = os.environ["LOAD_COOKIE"]
    report = run(
        args.url,
        users=args.users,
        requests=args.requests,
        timeout=args.timeout,
        expected_status=args.expected_status,
        headers=headers,
        pid=args.pid,
    )
    report["passed"] = (
        report["error_rate"] <= args.max_error_rate
        and report["latency_ms"]["p95"] <= args.max_p95_ms
        and report["qps"] >= args.min_qps
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n")
    sys.stdout.write(output + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
