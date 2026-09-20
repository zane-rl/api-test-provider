# -*- coding: utf-8 -*-
"""自测公共设施：起服务、发请求、按清单自动鉴权与翻页。

这里的「通用翻页器」刻意只读 `/__admin/scenarios` 给出的分页元数据来翻页——
如果清单和实现对不上，用例就会挂，等于顺带守住了清单的正确性。
"""

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlencode, urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server as server_module  # noqa: E402


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向：302 场景要能看到原始响应"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class Response:
    def __init__(self, status, headers, text):
        self.status = status
        self.headers = headers
        self.text = text

    @property
    def json(self):
        return json.loads(self.text)


def request(url, method="GET", headers=None, body=None, json_body=None,
            query=None, cookies=None, timeout=60):
    """发一个请求，4xx/5xx 不抛异常而是原样返回"""
    if query:
        sep = "&" if urlparse(url).query else "?"
        url = url + sep + urlencode(query)
    data = None
    headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else body
    if cookies:
        headers["Cookie"] = "; ".join("%s=%s" % (k, v) for k, v in cookies.items())
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return Response(resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        headers = dict(exc.headers)
        code = exc.code
        exc.close()          # 不关会刷 ResourceWarning
        return Response(code, headers, text)


# ==================== 取值 ====================

def extract(payload, path):
    """按点号路径取值；空串表示根"""
    if path in (None, ""):
        return payload
    cursor = payload
    for part in path.split("."):
        if cursor is None:
            return None
        cursor = cursor.get(part) if isinstance(cursor, dict) else None
    return cursor


def records_of(payload, record_path):
    value = extract(payload, record_path)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# ==================== 鉴权 ====================

def obtain_token(base_url, flow):
    """按清单里的 tokenFlow 取一个访问令牌"""
    kind = flow["kind"]
    if kind == "REQUEST_TOKEN":
        resp = request(flow["url"], method=flow["method"], json_body=flow["requestBody"])
        assert resp.status == 200, "取令牌失败：%s %s" % (resp.status, resp.text)
        return extract(resp.json, flow["tokenPath"])
    if kind == "OAUTH2_CLIENT_CREDENTIALS":
        form = {"grant_type": flow["grantType"]}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if flow["clientAuthMethod"] == "post":
            form["client_id"] = flow["clientId"]
            form["client_secret"] = flow["clientSecret"]
        else:
            import base64
            raw = "%s:%s" % (flow["clientId"], flow["clientSecret"])
            headers["Authorization"] = "Basic " + base64.b64encode(raw.encode()).decode()
        resp = request(flow["url"], method="POST", headers=headers, body=urlencode(form))
        assert resp.status == 200, "取令牌失败：%s %s" % (resp.status, resp.text)
        return extract(resp.json, flow["tokenPath"])
    raise AssertionError("未知的 tokenFlow：%s" % kind)


class Auth:
    """一次请求要带的鉴权材料"""

    def __init__(self, headers=None, query=None, cookies=None):
        self.headers = dict(headers or {})
        self.query = dict(query or {})
        self.cookies = dict(cookies or {})

    def merge_into(self, headers, query, cookies):
        headers.update(self.headers)
        query.update(self.query)
        cookies.update(self.cookies)


def build_auth(entry, token=None):
    """按 `/__admin/scenarios` 的鉴权描述拼出正确凭证"""
    spec = entry["auth"]
    auth = Auth()
    creds = list(spec["credentials"]) + [
        {"in": e["in"], "name": e["name"], "testValue": e["value"], "prefix": None}
        for e in spec["extraAuthParams"]]
    for cred in creds:
        value = cred["testValue"]
        if value == "<由令牌接口签发>":
            if token is None:
                token = obtain_token(entry["baseUrl"], spec["tokenFlow"])
            value = ("%s %s" % (cred["prefix"], token)) if cred["prefix"] else token
        place(auth, cred["in"], cred["name"], value)
    return auth


def place(auth, location, name, value):
    if location == "HEADER":
        auth.headers[name] = value
    elif location == "QUERY":
        auth.query[name] = value
    elif location == "COOKIE":
        auth.cookies[name] = value
    else:
        raise AssertionError("未知位置：%s" % location)


# ==================== 通用翻页器 ====================

def _call(url, method, auth, query=None, body=None, headers=None):
    q = dict(query or {})
    h = dict(headers or {})
    c = {}
    if auth:
        auth.merge_into(h, q, c)
    return request(url, method=method, headers=h, query=q, cookies=c,
                   json_body=body if body is not None else None)


def fetch_all(entry, auth=None, max_pages=100, follow_cross_origin=False):
    """按清单声明的分页方式翻到底，返回全部记录。

    NEXT_URL 默认只跟随同源地址（与后端约定一致）。
    """
    ptype = entry["paginationType"]
    pg = entry.get("pagination") or {}
    method = entry["method"]
    url = entry["url"]
    record_path = entry["recordPath"]
    records = []

    if ptype == "NONE":
        resp = _call(url, method, auth, body=_default_body(entry))
        assert resp.status == 200, "%s %s -> %s %s" % (method, url, resp.status, resp.text[:200])
        return records_of(resp.json, record_path)

    if ptype == "PAGE_NUMBER":
        page = pg.get("startFrom", 1)
        size = pg.get("defaultPageSize") or pg.get("fixedPageSize") or 20
        for _ in range(max_pages):
            query, body = {}, _default_body(entry)
            _put_param(pg.get("pageParam"), page, query, body)
            if pg.get("pageSizeParam"):
                _put_param(pg["pageSizeParam"], size, query, body)
            resp = _call(url, method, auth, query=query, body=body)
            assert resp.status == 200, "翻页失败：%s %s" % (resp.status, resp.text[:200])
            rows = records_of(resp.json, record_path)
            records.extend(rows)
            if not rows:
                break
            total = extract(resp.json, pg["totalPath"]) if pg.get("totalPath") else None
            has_more = extract(resp.json, pg["hasMorePath"]) if pg.get("hasMorePath") else None
            if has_more is False or (total is not None and len(records) >= total):
                break
            if len(rows) < size:
                break
            page += 1
        return records

    if ptype == "OFFSET":
        offset = pg.get("startFrom", 0)
        limit = pg.get("defaultLimit", 20)
        for _ in range(max_pages):
            query, body = {}, _default_body(entry)
            _put_param(pg["offsetParam"], offset, query, body)
            _put_param(pg["limitParam"], limit, query, body)
            resp = _call(url, method, auth, query=query, body=body)
            assert resp.status == 200, "翻页失败：%s %s" % (resp.status, resp.text[:200])
            rows = records_of(resp.json, record_path)
            records.extend(rows)
            total = extract(resp.json, pg["totalPath"]) if pg.get("totalPath") else None
            if not rows or (total is not None and len(records) >= total) or len(rows) < limit:
                break
            offset += limit
        return records

    if ptype == "CURSOR":
        cursor = None
        limit = pg.get("defaultLimit", 20)
        for _ in range(max_pages):
            query, body, headers = {}, _default_body(entry), {}
            if cursor is not None:
                _put_param(pg["cursorParam"], cursor, query, body, headers)
            _put_param(pg["limitParam"], limit, query, body, headers)
            resp = _call(url, method, auth, query=query, body=body, headers=headers)
            assert resp.status == 200, "翻页失败：%s %s" % (resp.status, resp.text[:200])
            rows = records_of(resp.json, record_path)
            records.extend(rows)
            cursor = extract(resp.json, pg["nextCursorPath"])
            has_more = extract(resp.json, pg["hasMorePath"]) if pg.get("hasMorePath") else None
            if not rows or has_more is False or cursor in (None, ""):
                break
        return records

    if ptype == "NEXT_URL":
        origin = _origin(url)
        current = url
        for _ in range(max_pages):
            resp = _call(current, method, auth)
            assert resp.status == 200, "翻页失败：%s %s" % (resp.status, resp.text[:200])
            records.extend(records_of(resp.json, record_path))
            nxt = extract(resp.json, pg["nextUrlPath"])
            if not nxt:
                break
            nxt = urljoin(current, nxt)
            if not follow_cross_origin and _origin(nxt) != origin:
                break
            current = nxt
        return records

    raise AssertionError("未知分页方式：%s" % ptype)


def _origin(url):
    parsed = urlparse(url)
    return (parsed.scheme, parsed.hostname, parsed.port)


def _put_param(param, value, query, body, headers=None):
    if not param:
        return
    where = param["in"]
    if where == "QUERY":
        query[param["name"]] = value
    elif where == "BODY":
        assert body is not None, "端点没有请求体，却要求把参数写进 BODY"
        body[param["name"]] = value
    elif where == "HEADER":
        assert headers is not None, "该分页方式不支持把参数放请求头"
        headers[param["name"]] = str(value)
    else:
        raise AssertionError("未知参数位置：%s" % where)


def _default_body(entry):
    """POST 端点的默认请求体（翻页参数由 _put_param 往里写）"""
    return {} if entry["method"] == "POST" else None


# ==================== 测试基类 ====================

class ProviderTestCase(unittest.TestCase):
    """每个测试类起一个随机端口的服务"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = server_module.create_server("127.0.0.1", 0, quiet=True)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = cls.httpd.advertise_base

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        resp = request(self.base + "/__admin/reset", method="POST")
        self.assertEqual(200, resp.status)

    # ---- 便捷方法 ----

    def catalog(self):
        resp = request(self.base + "/__admin/scenarios")
        self.assertEqual(200, resp.status)
        return {s["name"]: s for s in resp.json["scenarios"]}

    def scenario(self, name):
        return self.catalog()[name]

    def endpoint(self, scenario_name, path, method="GET"):
        for item in self.scenario(scenario_name)["endpoints"]:
            if item["path"] == path and item["method"] == method:
                return item
        raise AssertionError("场景 %s 下没有 %s %s" % (scenario_name, method, path))

    def set_config(self, **values):
        """调 /__admin/config（reset 不会动配置，用完记得自己恢复）"""
        resp = request(self.base + "/__admin/config", method="POST", json_body=values)
        assert resp.status == 200, resp.text
        return resp.json["config"]

    def requests_of(self, scenario_name, limit=200):
        resp = request("%s/__admin/requests?scenario=%s&limit=%d"
                       % (self.base, scenario_name, limit))
        self.assertEqual(200, resp.status)
        return resp.json["requests"]
