#!/usr/bin/env bash
# 跑全部自测（标准库 unittest，测试里自己在随机端口起服务）。
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m unittest discover -s tests -t . -p 'test_*.py' "$@"
