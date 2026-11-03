#!/usr/bin/env bash
# Run the test suite; exits non-zero on any failure (never pipe pytest through tail without pipefail).
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m pytest -q tests "$@"
