#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pytest --cov=services --cov=utils --cov=adapter --cov=main --cov=schemas --cov-branch --cov-report=term-missing --cov-fail-under=95 "$@"
