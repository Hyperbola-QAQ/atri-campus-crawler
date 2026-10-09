"""Require statement and branch coverage independently of the combined score."""

import json
import sys
from pathlib import Path


def check_report(report: dict, minimum: float = 95) -> None:
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
