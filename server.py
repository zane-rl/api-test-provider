#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试用 API 提供服务。

给 HDP「API 数据源」做全链路验证的被测对端：覆盖后端支持的全部鉴权 / 分页 / 请求体 /
响应 / 依赖 / 故障形态，每个场景自带一份与实现完全一致的 OpenAPI 文档。

    python3 server.py                # 监听 127.0.0.1:18090
    python3 server.py --port 9001

两个硬保证：
1. **数据确定可预期**：全部记录由固定公式 + 固定基准时间生成，重启完全一致；
   `/__admin/scenarios` 给出每个端点的 expectedTotal / recordPath / 分页参数。
2. **收到的请求可查询**：`/__admin/requests` 能查到服务端实际收到的请求（解码后的 Query、
   全部请求头、Cookie、原始请求体、响应码）。

代码里出现的所有凭证都是明显的测试假值，不含任何真实密钥。
"""

import argparse
import json
import threading
import traceback
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from scenarios import REGISTRY, reset_all
from scenarios import auth as auth_module
from scenarios import faults as faults_module
from scenarios.common import ApiError, Ctx, Resp, error_resp, json_resp, token_snapshot
from scenarios.openapi import build_openapi, build_swagger2

MAX_LOG = 5000


# ==================== 请求日志 ====================

class RequestLog:
    """内存里的请求日志，最多保留最近 MAX_LOG 条"""

    def __init__(self, maxlen=MAX_LOG):
        self._entries = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()

    def record(self, entry):
        with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            self._entries.append(entry)
            return entry

    def query(self, scenario=None, limit=100):
        with self._lock:
            rows = [e for e in self._entries
                    if scenario is None or e.get("scenario") == scenario]
        total = len(rows)
        if limit is not None and limit > 0:
            rows = rows[-limit:]
        return total, rows

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._seq = 0


# ==================== HTTP 处理 ====================

class Handler(BaseHTTPRequestHandler):
    server_version = "ApiTestProvider/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 基础设施 ----------

    def log_message(self, fmt, *args):
        if not self.server.quiet:
            print("[%s] %s %s" % (datetime.now().strftime("%H:%M:%S"),
                                  self.address_string(), fmt % args))

    def _write(self, resp):
        if resp.raw is not None:
            body = resp.raw
        elif resp.payload is not None:
            body = json.dumps(resp.payload, ensure_ascii=False).encode("utf-8")
        else:
            body = b""
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (resp.headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)
        return resp.status

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return ""
        raw = self.rfile.read(length)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    # ---------- 入口 ----------

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        query_all = parse_qs(parsed.query, keep_blank_values=True)
        body_raw = self._read_body()
        scenario_name = self._scenario_of(path)

        entry = None
        if not path.startswith("/__admin") and path != "/health":
            entry = {
                "time": datetime.now().isoformat(timespec="milliseconds"),
                "scenario": scenario_name,
                "method": method,
                "path": path,
                "query": {k: v[0] for k, v in query_all.items() if v},
                "queryAll": {k: list(v) for k, v in query_all.items()},
                "rawQuery": parsed.query,
                # 测试服务，凭证全是假值，请求头原样保留以便断言鉴权放对了位置
                "headers": {k: v for k, v in self.headers.items()},
                "cookies": {},
                "body": body_raw,
                "status": None,
            }

        try:
            resp = self._route(method, path, query_all, body_raw, scenario_name)
        except ApiError as exc:
            resp = error_resp(exc.status, exc.message, exc.headers)
        except Exception:                                    # noqa: BLE001
            traceback.print_exc()
            resp = error_resp(500, "服务内部错误（测试服务 bug，请看控制台栈）")

        status = self._write(resp)
        if entry is not None:
            entry["status"] = status
            entry["cookies"] = _parse_cookies(self.headers.get("Cookie"))
            self.server.request_log.record(entry)

    @staticmethod
    def _scenario_of(path):
        segments = [s for s in path.split("/") if s]
        if len(segments) >= 2 and segments[0] == "s":
            return unquote(segments[1])
        if path.startswith("/__admin"):
            return "__admin"
        if path == "/health":
            return "__health"
        return None

    # ---------- 路由 ----------

    def _route(self, method, path, query_all, body_raw, scenario_name):
        if path == "/health":
            return json_resp({"status": "ok"})

        if path.startswith("/__admin"):
            return self._admin(method, path, query_all, body_raw)

        # 写方法一律 405（真实只读 API 的行为；打到这里说明配置有误）
        if method in ("PUT", "PATCH", "DELETE"):
            return error_resp(405, "只读测试服务，不接受 %s" % method,
                              {"Allow": "GET, POST"})

        segments = [unquote(s) for s in path.split("/") if s]
        if not segments or segments[0] != "s" or len(segments) < 2:
            return error_resp(404, "无此路径：%s（业务路径形如 /s/<场景>/<端点>）" % path)

        scenario = REGISTRY.get(scenario_name)
        if scenario is None:
            return error_resp(404, "无此场景：%s（可用场景见 /__admin/scenarios）" % scenario_name)

        rest = segments[2:]
        base_url = "%s/s/%s" % (self.server.advertise_base, scenario.name)

        # 文档端点本身不需要鉴权，也不写进文档
        if rest == ["openapi.json"]:
            return json_resp(build_openapi(scenario, base_url))
        if rest == ["swagger2.json"]:
            if not scenario.swagger2:
                return error_resp(404, "场景 %s 未提供 Swagger 2.0 文档" % scenario.name)
            return json_resp(build_swagger2(scenario, base_url))

        endpoint, path_params = scenario.match(method, rest)
        if endpoint == "405":
            return error_resp(405, "路径存在但不支持 %s 方法" % method, {"Allow": "GET, POST"})
        if endpoint is None:
            return error_resp(404, "场景 %s 下无此端点：/%s" % (scenario.name, "/".join(rest)))

        ctx = Ctx(scenario.name, method, path, path_params, query_all,
                  dict(self.headers.items()), body_raw, base_url)
        ctx.raw_query = urlparse(self.path).query

        if endpoint.auth:
            denied = scenario.auth.check(ctx)
            if denied is not None:
                return denied
        return endpoint.handler(ctx)

    # ---------- admin ----------

    def _admin(self, method, path, query_all, body_raw):
        if method in ("PUT", "PATCH", "DELETE"):
            return error_resp(405, "只读测试服务，不接受 %s" % method, {"Allow": "GET, POST"})

        if path == "/__admin/scenarios" and method == "GET":
            return json_resp(self._scenarios_payload())

        if path == "/__admin/requests" and method == "GET":
            name = (query_all.get("scenario") or [None])[0]
            raw_limit = (query_all.get("limit") or ["100"])[0]
            try:
                limit = int(raw_limit)
            except ValueError:
                raise ApiError(400, "limit 必须是整数，收到：%r" % raw_limit)
            total, rows = self.server.request_log.query(name, limit)
            return json_resp({"scenario": name, "matched": total,
                              "returned": len(rows), "requests": rows})

        if path == "/__admin/reset" and method == "POST":
            self.server.request_log.clear()
            reset_all()
            return json_resp({"status": "ok", "message": "请求日志已清空，计数器与已签发令牌已复位",
                              "config": auth_module.config_snapshot()})

        if path == "/__admin/config":
            if method == "GET":
                return json_resp({"config": auth_module.config_snapshot()})
            patch = json.loads(body_raw) if body_raw.strip() else {}
            if not isinstance(patch, dict):
                raise ApiError(400, "请求体必须是 JSON 对象")
            return json_resp({"status": "ok", "config": auth_module.apply_config(patch)})

        if path == "/__admin/state" and method == "GET":
            return json_resp({"config": auth_module.config_snapshot(),
                              "faultCounters": faults_module.counters_snapshot(),
                              "issuedTokens": token_snapshot()})

        return error_resp(404, "无此 admin 接口：%s" % path)

    def _scenarios_payload(self):
        base = self.server.advertise_base
        items = []
        for scenario in REGISTRY.values():
            scenario_base = "%s/s/%s" % (base, scenario.name)
            items.append({
                "name": scenario.name,
                "group": getattr(scenario, "group", ""),
                "title": scenario.title,
                "covers": scenario.covers,
                "baseUrl": scenario_base,
                "openapiUrl": scenario_base + "/openapi.json",
                "swagger2Url": (scenario_base + "/swagger2.json") if scenario.swagger2 else None,
                "auth": scenario.auth.describe(scenario_base),
                "endpoints": [e.catalog(scenario_base) for e in scenario.endpoints],
            })
        return {"baseUrl": base, "count": len(items), "scenarios": items}


def _parse_cookies(raw):
    if not raw:
        return {}
    from http.cookies import SimpleCookie
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return {}
    return {k: m.value for k, m in jar.items()}


# ==================== 启动 ====================

def create_server(host="127.0.0.1", port=18090, quiet=False, advertise_host="127.0.0.1"):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.quiet = quiet
    httpd.request_log = RequestLog()
    # 对外自称地址：OpenAPI servers、下一页同源绝对地址都用它，不受 Host 头影响
    httpd.advertise_base = "http://%s:%d" % (advertise_host, httpd.server_address[1])
    return httpd


def main():
    parser = argparse.ArgumentParser(description="HDP API 数据源测试用 API 提供服务")
    parser.add_argument("--port", type=int, default=18090, help="监听端口，默认 18090")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--quiet", action="store_true", help="不打印每条请求日志")
    args = parser.parse_args()

    httpd = create_server(args.host, args.port, args.quiet)
    base = httpd.advertise_base
    endpoint_count = sum(len(s.endpoints) for s in REGISTRY.values())
    print("测试 API 服务已启动：%s" % base)
    print("  场景 %d 个 / 端点 %d 个" % (len(REGISTRY), endpoint_count))
    print("  场景清单：%s/__admin/scenarios" % base)
    print("  请求日志：%s/__admin/requests?scenario=<名字>&limit=50" % base)
    print("  健康检查：%s/health" % base)
    print("  某场景文档：%s/s/page-number/openapi.json" % base)
    print("  Ctrl+C 退出")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
