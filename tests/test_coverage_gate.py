"""A high combined score must not hide insufficient branch coverage."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "coverage_gate", Path(__file__).resolve().parents[1] / "scripts/check-coverage.py"
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.parametrize(
    "lines,branches,passes",
    [(100, 94, False), (94, 100, False), (95, 95, True), (96, 99, True)],
)
def test_independent_thresholds(lines, branches, passes):
    report = {
        "totals": {
            "covered_lines": lines,
            "num_statements": 100,
            "covered_branches": branches,
            "num_branches": 100,
        }
    }
    if passes:
        gate.check_report(report)
    else:
        with pytest.raises(SystemExit, match="Coverage below threshold"):
            gate.check_report(report)


def test_module_without_statements_or_branches_passes():
    gate.check_report(
        {
            "totals": {
                "covered_lines": 0,
                "num_statements": 0,
                "covered_branches": 0,
                "num_branches": 0,
            }
        }
    )
