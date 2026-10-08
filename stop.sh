#!/usr/bin/env bash
# 停掉本仓库起的测试用 API 提供服务（与 run.sh 配对）。
# 用法：./stop.sh              停 127.0.0.1:18090 上的实例
#       ./stop.sh --port 9001
# 只停「监听该端口、命令行是 server.py、工作目录是本仓库」的进程，别的进程一律不碰。
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd -P)"

PORT=18090
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?--port 需要端口号}"; shift 2 ;;
    --port=*) PORT="${1#--port=}"; shift ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "未知参数：${1}（用法：./stop.sh [--port 端口]）" >&2; exit 2 ;;
  esac
done
case "${PORT}" in ''|*[!0-9]*) echo "端口必须是数字，收到：${PORT}" >&2; exit 2 ;; esac

PIDS="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)"
if [ -z "$PIDS" ]; then
  echo "端口 ${PORT} 上没有进程在监听，无需停止"
  exit 0
fi

for PID in $PIDS; do
  CMD="$(ps -o command= -p "$PID" 2>/dev/null || true)"
  CWD="$(lsof -a -p "$PID" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
  # 不是本仓库的 server.py 就拒绝：宁可不停，也不误杀占了同一端口的别的程序
  case "$CMD" in
    *server.py*) ;;
    *) echo "端口 ${PORT} 被别的程序占用（PID ${PID}：${CMD}），不是本服务，未停止" >&2; exit 1 ;;
  esac
  if [ "$CWD" != "$ROOT" ]; then
    echo "端口 ${PORT} 上的 server.py（PID ${PID}）工作目录是 ${CWD:-未知}，不是本仓库 ${ROOT}，未停止" >&2
    exit 1
  fi
  kill "$PID"
  # 等它真正退出、端口释放；超时不强杀，交给人判断
  for _ in $(seq 1 50); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$PID" 2>/dev/null; then
    echo "PID ${PID} 在 5 秒内没有退出，请手动检查" >&2
    exit 1
  fi
  echo "已停止端口 ${PORT} 上的测试 API 服务（PID ${PID}）"
done
