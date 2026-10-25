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
import sys
import threading
import traceback
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from scenarios import REGISTRY, reset_all
from scenarios import auth as auth_module
from scenarios import faults as faults_module
from scenarios import negative as negative_module
from scenarios import faultswitch as faultswitch_module
from scenarios import switchable as switchable_module
from scenarios.common import ApiError, Ctx, Resp, error_resp, json_resp, token_snapshot
from scenarios.bundle import build_bundle, bundle_names
from scenarios.openapi import build_openapi
from scenarios.yamlout import dump as yaml_dump


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """兼容 Python 3.6 的线程化 HTTP 服务，避免空闲连接阻塞其他请求。"""

    daemon_threads = True


MAX_LOG = 5000
MAX_BODY_BYTES = 10 * 1024 * 1024      # 请求体上限，防止分块读取被撑爆
MAX_CHUNK_LINE = 65536                 # 块长度行 / trailer 行的最大长度

# 文档的三种扩展名：同一份文档对象，只是序列化方式不同（.yaml 与 .yml 字节完全相同）
DOC_EXTENSIONS = ("json", "yaml", "yml")


def doc_response(doc, ext):
    """把一份文档按扩展名输出：json 走 JSON，yaml / yml 走 YAML"""
    if ext == "json":
        return json_resp(doc)
    return Resp(raw=yaml_dump(doc).encode("utf-8"), content_type="application/yaml; charset=utf-8")


def doc_urls(prefix):
    """{扩展名: 地址}，prefix 是不带扩展名的文档地址"""
    return {ext: "%s.%s" % (prefix, ext) for ext in DOC_EXTENSIONS}


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

    def clear(self, scenario=None):
        """清空日志；给 scenario 则只清该场景的（序号不归零，避免与残留条目撞号）。

        返回清掉的条数。
        """
        with self._lock:
            if scenario is None:
                removed = len(self._entries)
                self._entries.clear()
                self._seq = 0
                return removed
            kept = [e for e in self._entries if e.get("scenario") != scenario]
            removed = len(self._entries) - len(kept)
            self._entries.clear()
            self._entries.extend(kept)
            return removed


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
        """读请求体。

        **唯一一处读体的地方**，所有端点都经过它。除 `Content-Length` 外还要支持
        `Transfer-Encoding: chunked`——JDK HttpClient 发 POST 时默认就是 chunked 且不带
        `Content-Length`（HTTP/1.1 合法），而 `http.server` 不会自动解码。
        """
        encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in encoding:
            raw = self._read_chunked()
        else:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return ""
            if length > MAX_BODY_BYTES:
                raise ApiError(413, "请求体超过上限 %d 字节" % MAX_BODY_BYTES)
            raw = self.rfile.read(length)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    def _read_chunked(self):
        """按 RFC 7230 §4.1 解码分块请求体。

        块长度行可能带 `;` 扩展；最后一块长度为 0，之后可能有 trailer，读到空行为止。
        任何格式错误都要把连接标记为关闭——此时字节流已经错位，keep-alive 复用会串包。
        """
        pieces = []
        total = 0
        while True:
            size_line = self.rfile.readline(MAX_CHUNK_LINE)
            if not size_line:
                raise self._chunk_error("读取块长度时连接提前结束")
            size_field = size_line.split(b";", 1)[0].strip()
            if not size_field:
                raise self._chunk_error("块长度行为空")
            try:
                size = int(size_field, 16)
            except ValueError:
                raise self._chunk_error("块长度不是十六进制：%r" % size_field[:40])
            if size < 0:
                raise self._chunk_error("块长度为负")
            if size == 0:
                break
            total += size
            if total > MAX_BODY_BYTES:
                self.close_connection = True
                raise ApiError(413, "分块请求体超过上限 %d 字节" % MAX_BODY_BYTES)
            data = self._read_exactly(size)
            pieces.append(data)
            tail = self.rfile.read(2)          # 每块数据后面跟一个 CRLF
            if tail not in (b"\r\n", b"\n\r", b"\n"):
                raise self._chunk_error("块数据后缺少 CRLF")
        # trailer：读到空行为止
        while True:
            line = self.rfile.readline(MAX_CHUNK_LINE)
            if not line or line in (b"\r\n", b"\n"):
                break
        return b"".join(pieces)

    def _read_exactly(self, size):
        """rfile.read 可能短读，循环补齐"""
        buf = bytearray()
        while len(buf) < size:
            chunk = self.rfile.read(size - len(buf))
            if not chunk:
                raise self._chunk_error("块数据不完整：期望 %d 字节，实收 %d" % (size, len(buf)))
            buf.extend(chunk)
        return bytes(buf)

    def _chunk_error(self, reason):
        self.close_connection = True
        return ApiError(400, "分块请求体格式错误：%s" % reason)

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
        scenario_name = self._scenario_of(path)
        body_error = None
        try:
            body_raw = self._read_body()
        except ApiError as exc:
            body_raw, body_error = "", exc

        entry = None
        # admin、健康检查、按鉴权合并的文档都不属于任何场景，不记日志
        if not path.startswith(("/__admin", "/openapi/")) and path != "/health":
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
            if body_error is not None:
                raise body_error
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
        if path.startswith(negative_module.PREFIX + "/"):
            return negative_module.NAME
        if path == "/health":
            return "__health"
        return None

    # ---------- 路由 ----------

    def _route(self, method, path, query_all, body_raw, scenario_name):
        if path == "/health":
            return json_resp({"status": "ok"})

        if path.startswith("/__admin"):
            return self._admin(method, path, query_all, body_raw)

        if path.startswith("/openapi/"):
            return self._bundle(method, path)

        # 写方法一律 405（真实只读 API 的行为；打到这里说明配置有误）
        if method in ("PUT", "PATCH", "DELETE"):
            return error_resp(405, "只读测试服务，不接受 %s" % method,
                              {"Allow": "GET, POST"})

        if path.startswith(negative_module.PREFIX + "/"):
            return self._negative(method, path, query_all, body_raw)

        segments = [unquote(s) for s in path.split("/") if s]
        if not segments or segments[0] != "s" or len(segments) < 2:
            return error_resp(404, "无此路径：%s（业务路径形如 /s/<场景>/<端点>）" % path)

        scenario = REGISTRY.get(scenario_name)
        if scenario is None:
            return error_resp(404, "无此场景：%s（可用场景见 /__admin/scenarios）" % scenario_name)

        rest = segments[2:]
        base_url = "%s/s/%s" % (self.server.advertise_base, scenario.name)

        # 文档端点本身不需要鉴权，也不写进文档；三种扩展名内容等价
        if len(rest) == 1 and rest[0] in ["openapi." + ext for ext in DOC_EXTENSIONS]:
            return doc_response(build_openapi(scenario, base_url), rest[0].rpartition(".")[2])

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

    # ---------- 按鉴权类型合并的文档 ----------

    def _bundle(self, method, path):
        """GET /openapi/<鉴权类型|all>.yaml / .json"""
        if method != "GET":
            return error_resp(405, "文档只支持 GET", {"Allow": "GET"})
        name, _, ext = path[len("/openapi/"):].rpartition(".")
        if ext not in DOC_EXTENSIONS or name not in bundle_names():
            return error_resp(404, "无此文档：%s（可用：%s，后缀 .yaml / .yml / .json）"
                              % (path, " / ".join(bundle_names())))
        return doc_response(build_bundle(name, self.server.advertise_base), ext)

    # ---------- 反例文档（应被后端拒绝的文档版本） ----------

    def _negative(self, method, path, query_all, body_raw):
        """/__negative/<swagger2|openapi31>.<json|yaml|yml> 与它们描述的 GET /__negative/items"""
        rest = [unquote(s) for s in path.split("/") if s][1:]
        base_url = self.server.advertise_base + negative_module.PREFIX
        if len(rest) == 1:
            name, _, ext = rest[0].rpartition(".")
            if name in negative_module.DOCUMENTS and ext in DOC_EXTENSIONS:
                return doc_response(
                    negative_module.build_document(name, self.server.advertise_base), ext)
        endpoint, path_params = negative_module.SCENARIO.match(method, rest)
        if endpoint == "405":
            return error_resp(405, "路径存在但不支持 %s 方法" % method, {"Allow": "GET"})
        if endpoint is None:
            return error_resp(404, "无此反例路径：%s（可用文档：%s，后缀 .json / .yaml / .yml）"
                              % (path, " / ".join(negative_module.DOCUMENTS)))
        return endpoint.handler(Ctx(negative_module.NAME, method, path, path_params,
                                    query_all, dict(self.headers.items()), body_raw, base_url))

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
            name = (query_all.get("scenario") or [None])[0]
            if name is not None and name not in REGISTRY and name != negative_module.NAME:
                raise ApiError(404, "无此场景：%s（可用场景见 /__admin/scenarios）" % name)
            removed = self.server.request_log.clear(name)
            reset_all(name)
            scope = ("场景 %s " % name) if name else "全部场景"
            return json_resp({
                "status": "ok", "scenario": name, "removedRequests": removed,
                "message": "%s的请求日志已清空，相关计数器与已签发令牌已复位（配置不变）" % scope,
                "config": auth_module.config_snapshot()})

        if path == "/__admin/config":
            if method == "GET":
                return json_resp({"config": auth_module.config_snapshot()})
            patch = json.loads(body_raw) if body_raw.strip() else {}
            if not isinstance(patch, dict):
                raise ApiError(400, "请求体必须是 JSON 对象")
            return json_resp({"status": "ok", "config": auth_module.apply_config(patch)})

        if path == "/__admin/faults":
            # 按接口的故障开关：每个有开关的场景一块独立的开关板，按场景名分发
            boards = faultswitch_module.BOARDS
            name = (query_all.get("scenario") or [None])[0]
            if not name:
                raise ApiError(400, "缺少参数 scenario（有故障开关的场景：%s）"
                               % " / ".join(boards))
            if name not in boards:
                raise ApiError(404, "场景 %s 没有故障开关（有开关的场景：%s）"
                               % (name, " / ".join(boards)))
            if method == "POST":
                try:
                    patch = json.loads(body_raw) if body_raw.strip() else None
                except ValueError:
                    raise ApiError(400, "请求体不是合法 JSON")
                boards[name].apply(patch)
            return json_resp({"scenario": name, "endpoints": boards[name].snapshot()})

        if path == "/__admin/state" and method == "GET":
            return json_resp({"config": auth_module.config_snapshot(),
                              "faultCounters": faults_module.counters_snapshot(),
                              "faultSwitches": switchable_module.switches_snapshot(),
                              # faultSwitches 只有 depend-switchable（先有的字段，保持原样）；
                              # 全部带开关的场景按场景名分组放在这里
                              "faultSwitchesByScenario": {
                                  name: board.snapshot()
                                  for name, board in faultswitch_module.BOARDS.items()},
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
                # 同一份文档的三种扩展名（内容等价）
                "openapiUrls": doc_urls(scenario_base + "/openapi"),
                "auth": scenario.auth.describe(scenario_base),
                "endpoints": [e.catalog(scenario_base) for e in scenario.endpoints],
            })
        bundles = {name: "%s/openapi/%s.yaml" % (base, name) for name in bundle_names()}
        bundle_urls = {name: doc_urls("%s/openapi/%s" % (base, name)) for name in bundle_names()}
        return {"baseUrl": base, "count": len(items), "openapiBundles": bundles,
                "openapiBundleUrls": bundle_urls,
                # 应被后端拒绝的文档版本，不是正常场景、不计入 count
                "negativeDocuments": negative_module.catalog(base, DOC_EXTENSIONS),
                "scenarios": items}


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

def normalize_public_base_url(value):
    """校验显式对外地址；只接受HTTP(S)源地址，不接收凭证、路径或查询参数。"""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme in ("http", "https") and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
                 and not any(char.isspace() for char in value)
                 and (port is None or 0 < port < 65536))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("对外地址须为 http(s)://IP或域名[:端口]，不能包含凭证、路径或查询参数")
    return "%s://%s" % (parsed.scheme, parsed.netloc)


def create_server(host="127.0.0.1", port=18090, quiet=False, advertise_host="127.0.0.1",
                  advertise_base_url=None):
    public_base = normalize_public_base_url(advertise_base_url) if advertise_base_url else None
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.quiet = quiet
    httpd.request_log = RequestLog()
    # 对外自称地址：OpenAPI servers、下一页同源绝对地址都用它，不受 Host 头影响
    httpd.advertise_base = public_base or "http://%s:%d" % (advertise_host, httpd.server_address[1])
    return httpd


def main():
    parser = argparse.ArgumentParser(description="HDP API 数据源测试用 API 提供服务")
    parser.add_argument("--port", type=int, default=18090, help="监听端口，默认 18090")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--quiet", action="store_true", help="不打印每条请求日志")
    parser.add_argument("--advertise-base-url", type=normalize_public_base_url,
                        help="客户端可访问的完整源地址，如 http://10.0.0.8:18090；默认保持本机地址")
    args = parser.parse_args()

    httpd = create_server(args.host, args.port, args.quiet, advertise_base_url=args.advertise_base_url)
    base = httpd.advertise_base
    endpoint_count = sum(len(s.endpoints) for s in REGISTRY.values())
    print("测试 API 服务已启动：%s" % base)
    print("  场景 %d 个 / 端点 %d 个" % (len(REGISTRY), endpoint_count))
    print("  场景清单：%s/__admin/scenarios" % base)
    print("  请求日志：%s/__admin/requests?scenario=<名字>&limit=50" % base)
    print("  健康检查：%s/health" % base)
    print("  某场景文档：%s/s/page-number/openapi.<json|yaml|yml>" % base)
    print("  按鉴权合并的文档：%s/openapi/<%s>.<yaml|yml|json>" % (base, "|".join(bundle_names())))
    print("  反例文档（应被拒绝）：%s%s/<%s>.<json|yaml|yml>"
          % (base, negative_module.PREFIX, "|".join(negative_module.DOCUMENTS)))
    print("  Ctrl+C 退出")
    sys.stdout.flush()      # 输出重定向到文件时也能立刻看到启动横幅
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
