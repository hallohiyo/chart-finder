#!/usr/bin/env bash
# macOS / Linux 용 실행 스크립트. 처음 실행하면 준비 작업을 한다.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "처음 실행이라 준비 작업을 합니다. 몇 분 걸립니다..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -e ".[ui]"
    echo "준비 완료."
fi

exec .venv/bin/python -m chartfinder.simple_ui
