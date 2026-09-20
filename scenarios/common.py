# -*- coding: utf-8 -*-
"""场景公共设施。

这里集中三样东西，目的都是**让「OpenAPI 文档 / 实际实现 / __admin 清单」三者不可能漂移**：

1. `Endpoint` —— 一个端点的处理函数、参数声明、清单元数据写在同一个构造调用里；
2. `AuthSpec` / `AuthRequirement` —— 同一个对象既执行鉴权校验、又生成 securityScheme、又产出清单里的测试凭证；
3. `infer_schema` —— 响应 schema 由「真的调一次 handler」推断出来，不手写。

另外提供确定性造数：所有数据由固定公式 + 固定基准时间生成，不用 now()、不用随机数，重启后完全一致。
"""

import base64
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, unquote

# ==================== 基础常量 ====================

# 固定基准时间：所有 ISO 时间字段都由它推导，保证跨进程、跨天完全一致
CST = timezone(timedelta(hours=8))
BASE_TIME = datetime(2026, 1, 1, 8, 0, 0, tzinfo=CST)

CATEGORIES = ["耗材", "整机", "配件", "包材"]

_RE_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def iso(dt):
    """ISO 8601 带时区字符串，例：2026-01-01T09:00:00+08:00"""
    return dt.replace(microsecond=0).isoformat()


def build_record(prefix, i, id_field="id"):
    """按固定公式造一条记录。

    刻意覆盖七种字段形态：字符串、整数、小数、布尔、ISO 时间字符串、null、嵌套对象，
    让后端的字段类型推断 / 对象展开 / 主键解析都有料可吃。
    """
    return {
        id_field: "%s-%05d" % (prefix, i),
        "name": "%s物料%d" % (prefix, i),
        "category": CATEGORIES[i % len(CATEGORIES)],
        "price": round(9.9 + i * 1.37, 2),
        "quantity": (i * 13) % 500,
        "active": i % 7 != 0,
        "updated_at": iso(BASE_TIME + timedelta(hours=i)),
        # 每 5 条一个 null，既让 schema 推断能推出 nullable，又能验证后端对 null 的处理
        "remark": None if i % 5 == 0 else "备注%d" % i,
        "spec": {
            "length": round(10 + i % 30 + 0.5, 1),
            "width": round(5 + i % 17 + 0.25, 2),
            "unit": "cm",
        },
    }


def build_dataset(prefix, count, id_field="id"):
    """造一批记录，序号从 1 开始"""
    return [build_record(prefix, i, id_field) for i in range(1, count + 1)]


# ==================== 请求 / 响应 ====================

class ApiError(Exception):
    """处理函数里抛它，由路由统一转成 JSON 错误体"""

    def __init__(self, status, message, headers=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or {}


class Resp:
    """一次响应。payload 走 JSON 序列化；raw 走原样字节（故障类的非 JSON 响应用）"""

    __slots__ = ("status", "payload", "raw", "content_type", "headers")

    def __init__(self, status=200, payload=None, raw=None,
                 content_type="application/json; charset=utf-8", headers=None):
        self.status = status
        self.payload = payload
        self.raw = raw
        self.content_type = content_type
        self.headers = headers or {}


def json_resp(payload, status=200, headers=None):
    return Resp(status=status, payload=payload, headers=headers)


def error_resp(status, message, headers=None):
    """统一错误体：{"code": <http状态码>, "message": "..."}"""
    return Resp(status=status, payload={"code": status, "message": message}, headers=headers)


class Ctx:
    """一次请求的上下文。处理函数只从这里取值，便于文档生成时用假上下文复用同一处理函数。"""

    def __init__(self, scenario, method, path, path_params, query_all,
                 headers, body_raw, base_url):
        self.scenario = scenario
        self.method = method
        self.path = path
        self.path_params = path_params or {}
        self.query_all = query_all or {}
        self.raw_headers = dict(headers or {})
        self._lower = {k.lower(): v for k, v in self.raw_headers.items()}
        self.body_raw = body_raw or ""
        self.base_url = base_url
        self.raw_query = ""          # 原始（未解码）查询串，echo 场景用它验证编码
        self._body_json = _UNSET
        self._form = None

    # ---- 取值 ----

    @property
    def query(self):
        """同名参数只取第一个值，值已解码"""
        return {k: v[0] for k, v in self.query_all.items() if v}

    def header(self, name):
        """请求头大小写不敏感"""
        return self._lower.get(name.lower())

    @property
    def cookies(self):
        raw = self.header("Cookie")
        if not raw:
            return {}
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return {}
        return {k: m.value for k, m in jar.items()}

    @property
    def body_json(self):
        """请求体按 JSON 解析；不是合法 JSON 返回 None"""
        if self._body_json is _UNSET:
            try:
                self._body_json = json.loads(self.body_raw) if self.body_raw else None
            except ValueError:
                self._body_json = None
        return self._body_json

    @property
    def form(self):
        """请求体按 application/x-www-form-urlencoded 解析（OAuth2 令牌端点用）"""
        if self._form is None:
            parsed = parse_qs(self.body_raw, keep_blank_values=True)
            self._form = {k: v[0] for k, v in parsed.items() if v}
        return self._form

    # ---- 带校验的取参 ----

    def int_query(self, name, default=None, minimum=None):
        raw = self.query.get(name)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except ValueError:
            raise ApiError(400, "参数 %s 必须是整数，收到：%r" % (name, raw))
        if minimum is not None and value < minimum:
            raise ApiError(400, "参数 %s 不能小于 %d，收到：%d" % (name, minimum, value))
        return value

    def json_body_or_400(self):
        if self.body_raw and self.body_json is None:
            raise ApiError(400, "请求体不是合法 JSON")
        return self.body_json or {}


class _Unset:
    pass


_UNSET = _Unset()


# ==================== 令牌仓库（bearer-request 与 oauth2 共用） ====================

_TOKENS = {}          # token -> {"scenario", "uses", "maxUses", "scope"}
_TOKEN_SEQ = {}       # scenario -> 已签发次数
_TOKEN_LOCK = threading.Lock()


def issue_token(scenario, max_uses=None, scope=None, prefix="tok"):
    """签发一个确定性令牌：`<prefix>-<场景>-<序号>`。序号在 reset 后归零，便于复现。"""
    with _TOKEN_LOCK:
        seq = _TOKEN_SEQ.get(scenario, 0) + 1
        _TOKEN_SEQ[scenario] = seq
        token = "%s-%s-%d" % (prefix, scenario, seq)
        _TOKENS[token] = {"scenario": scenario, "uses": 0, "maxUses": max_uses, "scope": scope}
        return token


def consume_token(scenario, token):
    """校验令牌并计一次使用。超过 maxUses 即判失效（按次数而非时间，保证可复现）。"""
    if not token:
        return False
    with _TOKEN_LOCK:
        rec = _TOKENS.get(token)
        if rec is None or rec["scenario"] != scenario:
            return False
        rec["uses"] += 1
        if rec["maxUses"] is not None and rec["uses"] > rec["maxUses"]:
            return False
        return True


def reset_tokens():
    with _TOKEN_LOCK:
        _TOKENS.clear()
        _TOKEN_SEQ.clear()


def token_snapshot():
    with _TOKEN_LOCK:
        return {k: dict(v) for k, v in _TOKENS.items()}


# ==================== 鉴权 ====================

class AuthRequirement:
    """一条鉴权要求 = 一个 OpenAPI securityScheme + 一次运行时校验 + 一行清单里的测试凭证。

    三者共用同一个对象，改一处必然三处同步。
    `extra=True` 表示这是「附加鉴权参数」（后端侧配在附加参数里，而不是鉴权主凭证），
    在清单里单独列进 extraAuthParams。
    """

    def __init__(self, scheme_name, location, name, validator, test_value,
                 scheme, note="", prefix=None, extra=False, hint=None):
        self.scheme_name = scheme_name
        self.location = location            # header / query / cookie
        self.name = name
        self.validator = validator          # (value, ctx) -> bool
        self.test_value = test_value        # 写进 /__admin/scenarios 的测试凭证
        self.scheme = scheme                # OpenAPI securityScheme 片段
        self.note = note
        self.prefix = prefix                # 取值前缀，如 "Token" / "Bearer"；无前缀为 None
        self.extra = extra
        self.hint = hint                    # 自定义失败提示，(value) -> str

    def value_from(self, ctx):
        if self.location == "header":
            return ctx.header(self.name)
        if self.location == "query":
            return ctx.query.get(self.name)
        if self.location == "cookie":
            return ctx.cookies.get(self.name)
        return None

    def ok(self, ctx):
        value = self.value_from(ctx)
        if value is None or value == "":
            return False
        return self.validator(value, ctx)

    def failure_message(self, ctx):
        value = self.value_from(ctx)
        where = "%s %s" % (self.location.upper(), self.name)
        if value is None or value == "":
            return "缺少凭证：%s" % where
        if self.hint is not None:
            return self.hint(value)
        if self.prefix and not value.startswith(self.prefix + " "):
            return "凭证格式错误：%s 缺少前缀 %s" % (where, self.prefix)
        return "凭证错误：%s" % where

    def describe(self):
        item = {
            "in": self.location.upper(),
            "name": self.name,
            "testValue": self.test_value,
            "prefix": self.prefix,
        }
        if self.note:
            item["note"] = self.note
        return item


def api_key_req(location, name, value, note="", prefix=None, scheme_name=None):
    """API Key：header / query / cookie 三种位置同一套写法。

    prefix 非空时，取值必须是 `<prefix> <value>`（如 `Authorization: Token test-key-123`）。
    """
    expected = ("%s %s" % (prefix, value)) if prefix else value
    description = "测试 API Key，固定值 %s" % value
    if prefix:
        description += "；值须带前缀 `%s `（完整值：%s）" % (prefix, expected)

    def _hint(v):
        where = "%s %s" % (location.upper(), name)
        if prefix and not v.startswith(prefix + " "):
            return "凭证格式错误：%s 缺少前缀 %s（应为 `%s`）" % (where, prefix, expected)
        return "凭证错误：%s" % where

    return AuthRequirement(
        scheme_name=scheme_name or ("apiKey%s" % location.capitalize()),
        location=location, name=name,
        validator=lambda v, ctx: v == expected,
        test_value=expected,
        scheme={"type": "apiKey", "in": location, "name": name, "description": description},
        note=note, prefix=prefix, hint=_hint,
    )


def extra_param_req(location, name, value, note=""):
    """附加鉴权参数（后端侧的「附加参数」），不作为主凭证"""
    req = api_key_req(location, name, value, note=note,
                      scheme_name="extra%s%s" % (location.capitalize(), _camel(name)))
    req.extra = True
    return req


def _camel(name):
    return "".join(part.capitalize() for part in re.split(r"[-_]", name) if part)


def bearer_static_req(token):
    return AuthRequirement(
        scheme_name="bearerAuth", location="header", name="Authorization",
        validator=lambda v, ctx: v == "Bearer " + token,
        test_value="Bearer " + token,
        scheme={"type": "http", "scheme": "bearer", "description": "固定测试令牌 %s" % token},
        note="静态令牌，直接填写", prefix="Bearer",
    )


def basic_req(user, password):
    expected = base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")
    return AuthRequirement(
        scheme_name="basicAuth", location="header", name="Authorization",
        validator=lambda v, ctx: v == "Basic " + expected,
        test_value="Basic " + expected,
        scheme={"type": "http", "scheme": "basic",
                "description": "测试账号 %s / %s" % (user, password)},
        note="用户名 %s，密码 %s" % (user, password), prefix="Basic",
    )


def dynamic_token_req(scenario, location, name, prefix=None, scheme=None, note=""):
    """令牌由登录接口 / 令牌端点动态签发，业务接口按位置校验并计次"""
    full_prefix = (prefix + " ") if prefix else ""

    def _validate(value, ctx):
        if full_prefix:
            if not value.startswith(full_prefix):
                return False
            value = value[len(full_prefix):]
        return consume_token(scenario, value)

    default_scheme = ({"type": "http", "scheme": "bearer"}
                      if location == "header" and name.lower() == "authorization"
                      else {"type": "apiKey", "in": location, "name": name})
    default_scheme = dict(default_scheme)
    default_scheme.setdefault("description", "令牌由本场景的令牌接口签发")
    return AuthRequirement(
        scheme_name="tokenAuth", location=location, name=name,
        validator=_validate, test_value="<由令牌接口签发>",
        scheme=scheme or default_scheme, note=note, prefix=prefix,
    )


class AuthSpec:
    """一个场景的鉴权：若干条要求的「与」关系。缺任一 → 401。"""

    def __init__(self, kind="none", requirements=(), flow=None, message=None):
        self.kind = kind                # none / bearer / apiKey / basic / oauth2 / fixedParam
        self.requirements = list(requirements)
        self.flow = flow or {}          # 令牌获取方式，describe 时补全为完整 URL
        self.message = message or "未授权"

    def check(self, ctx):
        """通过返回 None，失败返回 401 响应"""
        for req in self.requirements:
            if not req.ok(ctx):
                return error_resp(401, "%s：%s" % (self.message, req.failure_message(ctx)))
        return None

    def schemes(self):
        return {r.scheme_name: r.scheme for r in self.requirements}

    def security(self):
        """多条要求用 OpenAPI 的「与」写法：一个对象里列多个 scheme"""
        if not self.requirements:
            return []
        return [{r.scheme_name: [] for r in self.requirements}]

    def describe(self, base_url=""):
        """后端侧「该怎么配」的结构化信息"""
        flow = None
        if self.flow:
            flow = dict(self.flow)
            if "urlPath" in flow:
                flow["url"] = base_url + flow.pop("urlPath")
        return {
            "type": self.kind,
            "credentials": [r.describe() for r in self.requirements if not r.extra],
            "extraAuthParams": [
                {"name": r.name, "in": r.location.upper(), "value": r.test_value}
                for r in self.requirements if r.extra
            ],
            "tokenFlow": flow,
        }


NO_AUTH = AuthSpec()


# ==================== 端点与场景 ====================

def qp(name, type_="string", required=False, description="", example=None):
    """Query 参数声明"""
    schema = {"type": type_}
    if example is not None:
        schema["example"] = example
    return {"name": name, "in": "query", "required": required,
            "description": description, "schema": schema}


def hp(name, type_="string", required=False, description="", example=None):
    """Header 参数声明"""
    schema = {"type": type_}
    if example is not None:
        schema["example"] = example
    return {"name": name, "in": "header", "required": required,
            "description": description, "schema": schema}


def pp(name, type_="string", description=""):
    """Path 参数声明（永远必填）"""
    return {"name": name, "in": "path", "required": True,
            "description": description, "schema": {"type": type_}}


def json_body(schema, example=None, required=True):
    content = {"schema": schema}
    if example is not None:
        content["example"] = example
    return {"required": required, "content": {"application/json": content}}


def form_body(schema, example=None, required=True):
    content = {"schema": schema}
    if example is not None:
        content["example"] = example
    return {"required": required,
            "content": {"application/x-www-form-urlencoded": content}}


class Endpoint:
    """一个端点。处理函数、文档声明、清单元数据写在一起。"""

    def __init__(self, method, path, handler, operation_id, summary,
                 description="", parameters=(), request_body=None,
                 auth=True, role="DATA", pagination=None, record_path=None,
                 primary_key=None, dataset=None, expected_total=None,
                 doc_sample=None, response_schema=None, response_example=None,
                 extra_responses=None, note=""):
        self.method = method.upper()
        self.path = path
        self.handler = handler
        self.operation_id = operation_id
        self.summary = summary
        self.description = description
        self.parameters = list(parameters)
        self.request_body = request_body
        self.auth = auth                      # False = 该端点不需要鉴权（登录 / 令牌端点）
        self.role = role                      # DATA 业务数据 / TOKEN 登录与令牌 / FAULT 故障注入
        self.pagination = pagination or {"type": "NONE"}
        self.record_path = record_path
        self.primary_key = primary_key
        self.dataset = dataset                # 无参 callable，返回该端点全量记录
        self._expected_total = expected_total
        self.doc_sample = doc_sample or {}
        self.response_schema = response_schema
        self.response_example = response_example
        self.extra_responses = extra_responses or {}
        self.note = note
        self.segments = [s for s in path.split("/") if s != ""]

    def match(self, segments):
        """路径模板匹配，返回路径参数字典；不匹配返回 None"""
        if len(segments) != len(self.segments):
            return None
        params = {}
        for tpl, actual in zip(self.segments, segments):
            if tpl.startswith("{") and tpl.endswith("}"):
                params[tpl[1:-1]] = actual
            elif tpl != actual:
                return None
        return params

    def expected_total(self):
        if self._expected_total is not None:
            return self._expected_total
        if self.dataset is not None:
            return len(self.dataset())
        return None

    def sample_first3(self):
        if self.dataset is None:
            return None
        return self.dataset()[:3]

    def catalog(self, base_url):
        """写进 /__admin/scenarios 的一条端点说明"""
        item = {
            "method": self.method,
            "path": self.path,
            "url": base_url + self.path,
            "role": self.role,
            "summary": self.summary,
            "paginationType": self.pagination.get("type", "NONE"),
            "pagination": {k: v for k, v in self.pagination.items() if k != "type"},
            "recordPath": self.record_path,
            "primaryKey": self.primary_key,
            "expectedTotal": self.expected_total(),
            "sampleFirst3": self.sample_first3(),
            "requiresAuth": self.auth,
        }
        if self.note:
            item["note"] = self.note
        if (self.doc_sample or {}).get("path"):
            # 路径参数的可用样例值，供测试直接拼出可访问的地址
            item["pathSample"] = dict(self.doc_sample["path"])
        return item


class Scenario:
    """一个场景：独立前缀、独立鉴权、独立数据"""

    def __init__(self, name, title, covers, endpoints, auth=None,
                 description="", swagger2=False):
        self.name = name
        self.title = title
        self.covers = covers                  # 覆盖的后端能力，写进 README 与清单
        self.endpoints = list(endpoints)
        self.auth = auth or NO_AUTH
        self.description = description or title
        self.swagger2 = swagger2              # 是否额外提供 Swagger 2.0 文档

    def prefix(self):
        return "/s/" + self.name

    def match(self, method, rest_segments):
        """返回 (endpoint, path_params)；路径命中但方法不对返回 ('405', 允许的方法)"""
        path_hit = False
        for ep in self.endpoints:
            params = ep.match(rest_segments)
            if params is None:
                continue
            path_hit = True
            if ep.method == method:
                return ep, params
        if path_hit:
            return "405", None
        return None, None


# ==================== 响应 schema 推断 ====================

def infer_schema(value):
    """由真实响应推断 JSON Schema —— 文档与实现天然一致的关键"""
    return _clean(_infer(value))


def _infer(value):
    if value is None:
        return {"__null__": True}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        schema = {"type": "string"}
        if _RE_DATETIME.match(value):
            schema["format"] = "date-time"
        elif _RE_DATE.match(value):
            schema["format"] = "date"
        return schema
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {"type": "object"}}
        items = _infer(value[0])
        # 合并前 5 条：让「第 N 条才出现 null」这类字段也能推出 nullable
        for item in value[1:5]:
            items = _merge(items, _infer(item))
        return {"type": "array", "items": items}
    if isinstance(value, dict):
        return {"type": "object",
                "properties": {k: _infer(v) for k, v in value.items()}}
    return {"type": "string"}


def _merge(a, b):
    if a.get("__null__"):
        return _nullable(b)
    if b.get("__null__"):
        return _nullable(a)
    if a.get("type") == "object" and b.get("type") == "object":
        props = dict(a.get("properties") or {})
        for k, v in (b.get("properties") or {}).items():
            props[k] = _merge(props[k], v) if k in props else v
        out = {"type": "object", "properties": props}
        if a.get("nullable") or b.get("nullable"):
            out["nullable"] = True
        return out
    if a.get("type") == "array" and b.get("type") == "array":
        out = {"type": "array", "items": _merge(a["items"], b["items"])}
        if a.get("nullable") or b.get("nullable"):
            out["nullable"] = True
        return out
    if a.get("type") != b.get("type"):
        # 整数与小数混排按 number 描述，其余保留先出现的类型
        if {a.get("type"), b.get("type")} == {"integer", "number"}:
            out = {"type": "number"}
            if a.get("nullable") or b.get("nullable"):
                out["nullable"] = True
            return out
    return a


def _nullable(schema):
    out = dict(schema)
    out.pop("__null__", None)
    out["nullable"] = True
    out.setdefault("type", "string")
    return out


def _clean(schema):
    if not isinstance(schema, dict):
        return schema
    if schema.get("__null__"):
        return {"type": "string", "nullable": True}
    out = {}
    for k, v in schema.items():
        if k == "properties":
            out[k] = {pk: _clean(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _clean(v)
        else:
            out[k] = v
    return out


def truncate_example(value, max_items=2, max_depth=8, _depth=0):
    """文档里的 example：数组截到 2 条，避免超大响应把文档撑爆"""
    if _depth >= max_depth:
        return value
    if isinstance(value, list):
        return [truncate_example(v, max_items, max_depth, _depth + 1) for v in value[:max_items]]
    if isinstance(value, dict):
        return {k: truncate_example(v, max_items, max_depth, _depth + 1) for k, v in value.items()}
    return value
