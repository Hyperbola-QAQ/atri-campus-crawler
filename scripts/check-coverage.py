"""按 coverage JSON 原始计数独立检查语句与分支覆盖率，默认各 95%，失败阻止 CI 继续。
这是测试覆盖率要求，与仅供参考的注释率统计无关。
"""

import json
import sys
from pathlib import Path


def check_report(report: dict, minimum: float = 95) -> None:
    """读取 report[totals] 独立计算两项覆盖率，不用平均或显示舍入值抵消不足。
    零分母按 100%，任一低于 minimum 抛出 SystemExit；缺少报告字段向上传播。
    """
    totals = report["totals"]
    metrics = {
        "lines": (totals["covered_lines"], totals["num_statements"]),
        "branches": (totals["covered_branches"], totals["num_branches"]),
    }
    failures = []
    for name, (covered, total) in metrics.items():
        percent = covered / total * 100 if total else 100
        print(f"{name}: {covered}/{total} ({percent:.2f}%), required {minimum}%")
        if percent < minimum:
            failures.append(name)
    if failures:
        raise SystemExit("Coverage below threshold: " + ", ".join(failures))


if __name__ == "__main__":
    check_report(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
