#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pytest --cov=services --cov=utils --cov=adapter --cov=main --cov=schemas --cov-branch --cov-report=term-missing --cov-report=json:coverage.json --cov-report=xml:coverage.xml --cov-fail-under=95 "$@"
.venv/bin/python scripts/check-coverage.py coverage.json
