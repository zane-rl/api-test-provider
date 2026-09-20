#!/usr/bin/env bash
# 前台启动测试用 API 提供服务（Ctrl+C 退出）。
# 用法：./run.sh            监听 127.0.0.1:18090
#       ./run.sh --port 9001
set -euo pipefail
cd "$(dirname "$0")"
exec python3 server.py "$@"
