# -*- coding: utf-8 -*-
"""A 类：鉴权场景。

每个场景一个独立前缀、一份独立数据（记录 id 带场景名，便于看出数据串场）。
业务接口统一是不分页的 `GET /items`，20 条，`{"code":0,"data":{"items":[...]}}`；
凭证缺失 / 错误 / 位置不对一律 401 + `{"code":401,"message":"..."}`。
"""

import base64
import threading
import time

from .common import (
    Endpoint, Scenario, AuthSpec, ApiError,
    api_key_req, extra_param_req, bearer_static_req, basic_req, dynamic_token_req,
    build_dataset, json_resp, error_resp, issue_token, json_type_of,
    qp, hp, json_body, form_body,
    require_media_type, JSON_MEDIA_TYPE, FORM_MEDIA_TYPE,
)

# ==================== 测试凭证（全部是明显的假值） ====================

STATIC_TOKEN = "test-token-abc"
API_KEY = "test-key-123"
BASIC_USER, BASIC_PASS = "test", "test-pass"
LOGIN_USER, LOGIN_PASS = "test", "test-pass"
CLIENT_ID, CLIENT_SECRET = "test-client", "test-secret"
APP_KEY, APP_SECRET = "test-client", "test-secret"      # token-json-body / token-form-body 取 Token 用
EXTRA_APP_KEY = "extra-key-1"
FIXED_APP_ID, FIXED_SIGN, FIXED_TENANT = "app-001", "test-sign", "t-001"

# 可由 POST /__admin/config 调整的参数
CONFIG = {
    "oauth2BasicTokenMaxUses": 2,         # oauth2-basic：令牌可用次数，第 N+1 次业务请求 401
    "oauth2ExpiringTokenTtlSeconds": 40,  # oauth2-expiring：令牌有效秒数
    "oauth2PagedTokenMaxUses": 2,         # oauth2-paged-maxuses：同上，用于分页批次内触发 401 重取
    # oauth2-paged-expiring：令牌有效秒数。**别调过 60**——后端对 >60 秒的长效令牌
    # 换用「过期前 30 秒」规则，这个场景就观察不到目标现象了
    "oauth2PagedTtlSeconds": 35,
    # oauth2-paged-expiring：每个业务请求的服务端延迟。取 6 秒是算过的，见下方场景注册处
    "oauth2PagedDelaySeconds": 6,
    # token-slow：取令牌接口校验通过后延迟多少秒再返回
    "tokenSlowDelaySeconds": 5,
}
TOKEN_SLOW_DEFAULT_SECONDS = CONFIG["tokenSlowDelaySeconds"]
_CONFIG_LOCK = threading.Lock()


# 每个配置项的取值范围（下限, 上限）。延迟允许为 0——自测要靠它把用例跑快
CONFIG_BOUNDS = {
    "oauth2BasicTokenMaxUses": (1, 1000),
    "oauth2ExpiringTokenTtlSeconds": (1, 3600),
    "oauth2PagedTokenMaxUses": (1, 1000),
    "oauth2PagedTtlSeconds": (1, 3600),
    "oauth2PagedDelaySeconds": (0, 60),
    "tokenSlowDelaySeconds": (0, 60),
}


def apply_config(patch):
    """调整可变参数；未知键或超出范围报 400"""
    with _CONFIG_LOCK:
        for key, value in (patch or {}).items():
            if key not in CONFIG:
                raise ApiError(400, "未知配置项：%s（可选：%s）" % (key, ", ".join(CONFIG)))
            low, high = CONFIG_BOUNDS[key]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ApiError(400, "配置项 %s 必须是整数，收到：%r" % (key, value))
            if value < low or value > high:
                raise ApiError(400, "配置项 %s 取值须在 %d~%d 之间，收到：%d"
                               % (key, low, high, value))
            CONFIG[key] = value
        return dict(CONFIG)


def config_snapshot():
    with _CONFIG_LOCK:
        return dict(CONFIG)


# ==================== 业务接口（所有鉴权场景共用形态） ====================

def _items_endpoint(rows, summary="查询数据项列表"):
    def handler(ctx):
        return json_resp({"code": 0, "message": "ok", "data": {"items": rows}})

    return Endpoint(
        "GET", "/items", handler,
        operation_id="listItems", summary=summary,
        description="读取数据项，不分页，一次返回全部 %d 条" % len(rows),
        role="DATA", record_path="data.items", primary_key="id",
        dataset=lambda: list(rows), doc_sample={},
    )


def _dataset_for(name):
    return build_dataset(name.upper(), 20)


# token-typed-fields：取 Token 返回里多带的字段（布尔两个、小数一个）
TYPED_TOKEN_EXTRA_TOP = {"success": True}
TYPED_TOKEN_EXTRA_DATA = {"refreshable": True, "refreshRatio": 0.8}

# oauth2-scope-required：令牌接口唯一接受的授权范围（与 securityScheme 声明的 scope 一致）
REQUIRED_SCOPE = "read"


# ==================== 登录 / 令牌端点 ====================

def _login_endpoint(scenario_name, inject_desc, extra_top=None, extra_data=None,
                    delay_key=None, extra_description=""):
    """调用接口获取 Token 的登录端点。

    operationId / summary 用明显的写语义词（login），避免被后端判成读取接口而误勾选。

    extra_top / extra_data：在响应顶层 / data 里多返回的固定字段（`token-typed-fields` 用来带
    布尔与小数字段）；delay_key：校验通过后按该配置项延迟若干秒再返回（`token-slow`）。
    都不给时就是最基本的登录端点。
    """
    extra_top = dict(extra_top or {})
    extra_data = dict(extra_data or {})

    def payload_of(token):
        payload = {"code": 0, "message": "ok"}
        payload.update(extra_top)
        payload["data"] = dict({"accessToken": token, "expiresIn": 3600, "tokenType": "Bearer"},
                               **extra_data)
        return payload

    def handler(ctx):
        body = ctx.json_body_or_400()
        if body.get("username") != LOGIN_USER or body.get("password") != LOGIN_PASS:
            return error_resp(401, "登录失败：用户名或密码错误")
        # 账号密码错误立即返回，只有校验通过才延迟；doc_mode 下不 sleep
        if delay_key and not ctx.doc_mode:
            seconds = config_snapshot()[delay_key]
            if seconds:
                time.sleep(seconds)
        return json_resp(payload_of(issue_token(scenario_name)))

    data_props = {"accessToken": {"type": "string"},
                  "expiresIn": {"type": "integer"},
                  "tokenType": {"type": "string"}}
    data_props.update({k: {"type": json_type_of(v)} for k, v in extra_data.items()})
    props = {"code": {"type": "integer"}, "message": {"type": "string"}}
    props.update({k: {"type": json_type_of(v)} for k, v in extra_top.items()})
    props["data"] = {"type": "object", "properties": data_props}

    return Endpoint(
        "POST", "/login", handler,
        operation_id="login", summary="登录并签发访问令牌（写操作，不要勾选为数据接口）",
        description="账号 %s / %s。令牌在 data.accessToken，%s%s"
                    % (LOGIN_USER, LOGIN_PASS, inject_desc, extra_description),
        role="TOKEN", auth=False,
        request_body=json_body(
            {"type": "object", "required": ["username", "password"],
             "properties": {"username": {"type": "string"}, "password": {"type": "string"}}},
            {"username": LOGIN_USER, "password": LOGIN_PASS}),
        doc_sample=None,
        response_schema={"type": "object", "properties": props},
        response_example=payload_of("tok-%s-1" % scenario_name),
    )


def _oauth_token_endpoint(scenario_name, auth_method, reject_extra_header=False,
                          max_uses_key=None, ttl_key=None, required_scope=None):
    """OAuth2 客户端凭证令牌端点。

    auth_method=post  ：client_id / client_secret 放表单体；带了 Authorization 头 → 400
    auth_method=basic ：客户端凭证必须在 Authorization: Basic 头里；表单体出现 client_secret → 400

    令牌失效策略与客户端认证方式**解耦**，由配置键指定（两者可同时给，互不影响）：
    `max_uses_key` 按使用次数失效，`ttl_key` 按时间失效，`expires_in` 随之返回实际有效秒数。
    """

    def handler(ctx):
        form = ctx.form
        if reject_extra_header and ctx.header("X-App-Key") is not None:
            return json_resp({"error": "invalid_request",
                              "error_description": "令牌请求不应携带附加鉴权参数 X-App-Key"}, status=400)
        if form.get("grant_type") != "client_credentials":
            return json_resp({"error": "unsupported_grant_type",
                              "error_description": "只支持 grant_type=client_credentials"}, status=400)

        if auth_method == "post":
            if ctx.header("Authorization") is not None:
                return json_resp({"error": "invalid_request",
                                  "error_description": "本端点要求 client_secret_post：客户端凭证应放表单体，不应带 Authorization 头"},
                                 status=400)
            if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
                return json_resp({"error": "invalid_client",
                                  "error_description": "客户端凭证错误"}, status=401)
        else:
            if "client_secret" in form:
                return json_resp({"error": "invalid_request",
                                  "error_description": "本端点要求 client_secret_basic：表单体不应出现 client_secret"},
                                 status=400)
            raw = ctx.header("Authorization") or ""
            if not raw.startswith("Basic "):
                return json_resp({"error": "invalid_client",
                                  "error_description": "缺少 Authorization: Basic 客户端凭证"}, status=401)
            try:
                decoded = base64.b64decode(raw[6:]).decode("utf-8")
            except Exception:
                return json_resp({"error": "invalid_client",
                                  "error_description": "Basic 凭证不是合法 base64"}, status=401)
            user, _, pwd = decoded.partition(":")
            if user != CLIENT_ID or pwd != CLIENT_SECRET:
                return json_resp({"error": "invalid_client",
                                  "error_description": "客户端凭证错误"}, status=401)

        config = config_snapshot()
        max_uses = config[max_uses_key] if max_uses_key else None
        ttl = config[ttl_key] if ttl_key else None
        scope = form.get("scope") or ""
        # 只有声明了 required_scope 的场景才校验授权范围；放在客户端凭证校验之后
        if required_scope is not None and scope != required_scope:
            return json_resp(
                {"error": "invalid_scope",
                 "error_description": "本端点只接受 scope=%s，收到：%s"
                                      % (required_scope, ("%r" % scope) if scope else "（未提供）")},
                status=400)
        token = issue_token(scenario_name, max_uses=max_uses, scope=scope, prefix="oat",
                            ttl_seconds=ttl)
        payload = {"access_token": token, "token_type": "Bearer",
                   "expires_in": ttl if ttl else 3600}
        if scope:
            payload["scope"] = scope
        return json_resp(payload)

    if auth_method == "post":
        props = {"grant_type": {"type": "string", "example": "client_credentials"},
                 "client_id": {"type": "string", "example": CLIENT_ID},
                 "client_secret": {"type": "string", "example": CLIENT_SECRET},
                 "scope": {"type": "string"}}
        required = ["grant_type", "client_id", "client_secret"]
        example = {"grant_type": "client_credentials", "client_id": CLIENT_ID,
                   "client_secret": CLIENT_SECRET}
        sample_body = "grant_type=client_credentials&client_id=%s&client_secret=%s" % (CLIENT_ID, CLIENT_SECRET)
        sample_headers = {}
        desc = "客户端认证方式 client_secret_post：凭证放表单体；带 Authorization 头会返回 400"
    else:
        props = {"grant_type": {"type": "string", "example": "client_credentials"},
                 "scope": {"type": "string"}}
        required = ["grant_type"]
        example = {"grant_type": "client_credentials"}
        sample_body = "grant_type=client_credentials"
        sample_headers = {"Authorization": "Basic " + base64.b64encode(
            ("%s:%s" % (CLIENT_ID, CLIENT_SECRET)).encode()).decode()}
        desc = "客户端认证方式 client_secret_basic：凭证放 Authorization: Basic 头；表单体出现 client_secret 会返回 400"
    if required_scope is not None:
        required = required + ["scope"]
        props["scope"] = {"type": "string", "example": required_scope}
        example = dict(example, scope=required_scope)
        desc += ("；scope 必填且必须恰好是 %s，不带、为空或带别的值返回 400 "
                 "{\"error\": \"invalid_scope\"}" % required_scope)

    return Endpoint(
        "POST", "/oauth/token", handler,
        operation_id="issueToken", summary="签发 OAuth2 访问令牌（写操作，不要勾选为数据接口）",
        description=desc, role="TOKEN", auth=False,
        request_body=form_body({"type": "object", "required": required, "properties": props}, example),
        doc_sample=None,
        response_schema={"type": "object", "properties": {
            "access_token": {"type": "string"}, "token_type": {"type": "string"},
            "expires_in": {"type": "integer"}, "scope": {"type": "string"}}},
        response_example=dict({"access_token": "oat-%s-1" % scenario_name,
                               "token_type": "Bearer", "expires_in": 3600},
                              **({"scope": required_scope} if required_scope is not None else {})),
    )


# ==================== 场景定义 ====================

def _simple(name, title, covers, auth, extra_endpoints=()):
    rows = _dataset_for(name)
    endpoints = list(extra_endpoints) + [_items_endpoint(rows)]
    return Scenario(name=name, title=title, covers=covers, endpoints=endpoints, auth=auth)


def _bearer_request_scenario(name, location, header_name, prefix, title, covers,
                             flow_extra=None, **login_options):
    """Bearer「调用接口获取 Token」三种令牌注入位置。

    flow_extra：额外写进清单 tokenFlow 的字段；login_options 透传给 `_login_endpoint`
    （附加返回字段、延迟），供 `token-typed-fields` / `token-slow` 复用同一套鉴权。
    """
    inject = {"in": location.upper(), "name": header_name, "prefix": prefix}
    inject_desc = "注入到 %s %s%s" % (location.upper(), header_name,
                                     ("，带前缀 %s" % prefix) if prefix else "，不带前缀")
    flow = {"kind": "REQUEST_TOKEN", "method": "POST", "urlPath": "/login",
            "contentType": "application/json",
            "requestBody": {"username": LOGIN_USER, "password": LOGIN_PASS},
            "tokenPath": "data.accessToken", "expiresInPath": "data.expiresIn",
            "inject": inject}
    flow.update(flow_extra or {})
    auth = AuthSpec(
        kind="bearer",
        requirements=[dynamic_token_req(name, location, header_name, prefix=prefix,
                                        note=inject_desc)],
        flow=flow,
        message="未授权",
    )
    return _simple(name, title, covers, auth,
                   [_login_endpoint(name, inject_desc, **login_options)])


def _oauth2_auth(name, auth_method, extra=None, max_uses_key=None, ttl_key=None,
                 note=None):
    """OAuth2 客户端凭证鉴权：securityScheme、运行时校验、清单里的 tokenFlow 共用这一处"""
    reqs = [dynamic_token_req(name, "header", "Authorization", prefix="Bearer",
                              scheme={"type": "oauth2", "flows": {"clientCredentials": {
                                  "tokenUrl": "{base}/oauth/token",
                                  "scopes": {"read": "只读"}}},
                                  "description": "客户端凭证模式，客户端认证方式 client_secret_%s" % auth_method})]
    if extra is not None:
        reqs.append(extra)
    flow = {"kind": "OAUTH2_CLIENT_CREDENTIALS", "method": "POST", "urlPath": "/oauth/token",
            "grantType": "client_credentials", "clientAuthMethod": auth_method,
            "clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET, "scope": "read",
            "tokenPath": "access_token", "expiresInPath": "expires_in",
            "inject": {"in": "HEADER", "name": "Authorization", "prefix": "Bearer"}}
    # 写成 callable：/__admin/config 改了之后清单要能反映出来
    if max_uses_key:
        flow["tokenMaxUses"] = lambda: config_snapshot()[max_uses_key]
    if ttl_key:
        flow["expiresInSeconds"] = lambda: config_snapshot()[ttl_key]
    if note:
        flow["note"] = note
    return AuthSpec(kind="oauth2", requirements=reqs, flow=flow)


def _oauth2_scenario(name, auth_method, title, covers, extra=None,
                     max_uses_key=None, ttl_key=None, note=None, required_scope=None):
    auth = _oauth2_auth(name, auth_method, extra, max_uses_key, ttl_key, note)
    token_ep = _oauth_token_endpoint(name, auth_method,
                                     reject_extra_header=(extra is not None),
                                     max_uses_key=max_uses_key, ttl_key=ttl_key,
                                     required_scope=required_scope)
    return _simple(name, title, covers, auth, [token_ep])


# ---- 分页 + OAuth2：让一个同步批次里有多次业务请求，才观察得到令牌复用与重取 ----

PAGED_TOTAL = 50
PAGED_PAGE_SIZE = 10


def _paged_items_endpoint(rows, delay_key=None, note=""):
    """页码分页的数据接口，共 %d 条、默认每页 %d 条（5 页）""" % (PAGED_TOTAL, PAGED_PAGE_SIZE)

    def handler(ctx):
        # doc_mode 是生成文档时的假上下文，不能在那里 sleep
        if delay_key and not ctx.doc_mode:
            seconds = config_snapshot()[delay_key]
            if seconds:
                time.sleep(seconds)
        page = ctx.int_query("page", 1)
        size = max(1, min(ctx.int_query("page_size", PAGED_PAGE_SIZE), 200))
        start = (page - 1) * size
        if start < 0:
            raise ApiError(400, "页码不能小于 1")
        chunk = rows[start:start + size]
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": chunk, "page": page, "page_size": size,
                                   "total": len(rows),
                                   "has_next": start + size < len(rows)}})

    pagination = {"type": "PAGE_NUMBER",
                  "pageParam": {"name": "page", "in": "QUERY"},
                  "pageSizeParam": {"name": "page_size", "in": "QUERY"},
                  "startFrom": 1, "defaultPageSize": PAGED_PAGE_SIZE,
                  "totalPath": "data.total", "hasMorePath": "data.has_next"}
    if delay_key:
        pagination["pageDelaySeconds"] = lambda: config_snapshot()[delay_key]

    return Endpoint(
        "GET", "/items", handler,
        operation_id="listItems", summary="查询数据项列表（页码分页）",
        description="共 %d 条，默认每页 %d 条，取完需 %d 次请求"
                    % (len(rows), PAGED_PAGE_SIZE, len(rows) // PAGED_PAGE_SIZE),
        parameters=[qp("page", "integer", description="页码，从 1 开始"),
                    qp("page_size", "integer", description="每页数量，默认 %d" % PAGED_PAGE_SIZE)],
        role="DATA", record_path="data.items", primary_key="id",
        dataset=lambda: list(rows), pagination=pagination,
        doc_sample={"query": {"page": "1", "page_size": str(PAGED_PAGE_SIZE)}},
        note=note,
    )


def _paged_oauth2_scenario(name, title, covers, max_uses_key=None, ttl_key=None,
                           delay_key=None, flow_note=None, items_note=""):
    auth = _oauth2_auth(name, "post", max_uses_key=max_uses_key, ttl_key=ttl_key,
                        note=flow_note)
    token_ep = _oauth_token_endpoint(name, "post", max_uses_key=max_uses_key,
                                     ttl_key=ttl_key)
    rows = build_dataset(name.upper(), PAGED_TOTAL)
    items_ep = _paged_items_endpoint(rows, delay_key=delay_key, note=items_note)
    return Scenario(name, title, covers, [token_ep, items_ep], auth=auth)


# ---- 自定义取 Token 接口：只认一种请求体类型，Token 字段路径不是常见的 access_token ----

TOKEN_BODY_EXPIRES_IN = 7200


def _token_body_scenario(name, media_type, title, covers):
    """Bearer「调用接口获取 Token」的两种请求体形态。

    media_type=application/json：只收 JSON，Token 在嵌套路径 data.token.value；
    media_type=application/x-www-form-urlencoded：只收表单，Token 在顶层 tokenValue。
    用另一种类型提交一律 415；业务接口 /items 用 Authorization: Bearer 校验。
    """
    is_json = media_type == JSON_MEDIA_TYPE
    token_path = "data.token.value" if is_json else "tokenValue"
    expires_path = "data.token.expiresIn" if is_json else "expiresIn"
    credentials = {"appKey": APP_KEY, "appSecret": APP_SECRET}

    def wrap(token):
        if is_json:
            return {"code": 0, "message": "ok",
                    "data": {"token": {"value": token, "expiresIn": TOKEN_BODY_EXPIRES_IN,
                                       "type": "Bearer"}}}
        return {"tokenValue": token, "expiresIn": TOKEN_BODY_EXPIRES_IN, "tokenType": "Bearer"}

    def handler(ctx):
        require_media_type(ctx, media_type)
        body = ctx.json_body_or_400() if is_json else ctx.form
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象")
        if body.get("appKey") != APP_KEY or body.get("appSecret") != APP_SECRET:
            return error_resp(401, "取 Token 失败：appKey 或 appSecret 错误")
        return json_resp(wrap(issue_token(name)))

    how = ("Token 获取地址：POST {base}/auth/token，请求体类型 %s，字段 appKey=%s、appSecret=%s；"
           "Token 字段路径 %s，有效期（秒）字段路径 %s；"
           "业务接口放在请求头 Authorization，带前缀 Bearer"
           % (media_type, APP_KEY, APP_SECRET, token_path, expires_path))
    body_schema = {"type": "object", "required": ["appKey", "appSecret"],
                   "properties": {"appKey": {"type": "string"}, "appSecret": {"type": "string"}}}
    str_field, int_field = {"type": "string"}, {"type": "integer"}
    if is_json:
        response_schema = {"type": "object", "properties": {
            "code": int_field, "message": str_field,
            "data": {"type": "object", "properties": {
                "token": {"type": "object", "properties": {
                    "value": str_field, "expiresIn": int_field, "type": str_field}}}}}}
    else:
        response_schema = {"type": "object", "properties": {
            "tokenValue": str_field, "expiresIn": int_field, "tokenType": str_field}}

    token_ep = Endpoint(
        "POST", "/auth/token", handler,
        operation_id="issueAccessToken", summary="签发访问 Token（写操作，不要勾选为数据接口）",
        description="只接受 %s 请求体，用其它 Content-Type 提交返回 415；appKey / appSecret 错误返回 401。"
                    "Token 在响应的 %s，有效期（秒）在 %s" % (media_type, token_path, expires_path),
        role="TOKEN", auth=False,
        request_body=(json_body if is_json else form_body)(body_schema, credentials),
        doc_sample=None, response_schema=response_schema,
        response_example=wrap("tok-%s-1" % name),
    )
    auth = AuthSpec(
        kind="bearer",
        requirements=[dynamic_token_req(
            name, "header", "Authorization", prefix="Bearer",
            scheme={"type": "http", "scheme": "bearer", "description": how},
            note="注入到 HEADER Authorization，带前缀 Bearer")],
        flow={"kind": "REQUEST_TOKEN", "method": "POST", "urlPath": "/auth/token",
              "contentType": media_type, "requestBody": credentials,
              "tokenPath": token_path, "expiresInPath": expires_path,
              "expiresInSeconds": TOKEN_BODY_EXPIRES_IN,
              "inject": {"in": "HEADER", "name": "Authorization", "prefix": "Bearer"}},
    )
    return Scenario(name, title, covers, [token_ep, _items_endpoint(_dataset_for(name))],
                    auth=auth, description="%s。%s" % (title, how))


def _forbidden_scenario():
    name = "forbidden"
    rows = _dataset_for(name)

    def secret_handler(ctx):
        return error_resp(403, "无权访问该资源：凭证正确但缺少权限")

    secret = Endpoint(
        "GET", "/secret-items", secret_handler,
        operation_id="listSecretItems", summary="查询受限数据项",
        description="凭证正确也恒定返回 403，用于验证 401 与 403 的分类",
        role="DATA", record_path="data.items", primary_key="id",
        expected_total=0, doc_sample={},
        response_schema={"type": "object", "properties": {
            "code": {"type": "integer"}, "message": {"type": "string"}}},
        response_example={"code": 403, "message": "无权访问该资源：凭证正确但缺少权限"},
        note="恒定 403",
    )
    auth = AuthSpec(kind="bearer", requirements=[bearer_static_req(STATIC_TOKEN)])
    return _simple(name, "403 权限不足", "401 与 403 分类", auth, [secret])


def build_scenarios():
    scenarios = [
        _simple("none", "无鉴权", "鉴权方式 NONE",
                AuthSpec(kind="none")),
        _simple("bearer-static", "Bearer 静态令牌", "BEARER_TOKEN + tokenSource=STATIC",
                AuthSpec(kind="bearer", requirements=[bearer_static_req(STATIC_TOKEN)])),
        _bearer_request_scenario("bearer-request", "header", "Authorization", "Bearer",
                                 "Bearer 调接口取令牌（Authorization 头）",
                                 "BEARER_TOKEN + tokenSource=REQUEST，令牌注入 Header Authorization"),
        _bearer_request_scenario("bearer-request-header", "header", "X-Access-Token", None,
                                 "Bearer 调接口取令牌（自定义头，无前缀）",
                                 "tokenSource=REQUEST，令牌注入自定义 Header、不带 Bearer 前缀"),
        _bearer_request_scenario("bearer-request-query", "query", "access_token", None,
                                 "Bearer 调接口取令牌（Query）",
                                 "tokenSource=REQUEST，令牌注入 Query"),
        _simple("apikey-header", "API Key（请求头）", "API_KEY in=header",
                AuthSpec(kind="apiKey", requirements=[api_key_req("header", "X-Api-Key", API_KEY)])),
        _simple("apikey-query", "API Key（Query）", "API_KEY in=query",
                AuthSpec(kind="apiKey", requirements=[api_key_req("query", "api_key", API_KEY)])),
        _simple("apikey-cookie", "API Key（Cookie）", "API_KEY in=cookie",
                AuthSpec(kind="apiKey", requirements=[api_key_req("cookie", "api_key", API_KEY)])),
        _simple("apikey-header-prefix", "API Key（Authorization 头 + 前缀）",
                "API_KEY 放 Authorization 头且带自定义前缀 Token",
                AuthSpec(kind="apiKey",
                         requirements=[api_key_req("header", "Authorization", API_KEY,
                                                   prefix="Token",
                                                   note="完整值 `Token %s`，无前缀或前缀不符一律 401" % API_KEY)])),
        _simple("apikey-extra", "API Key + 附加鉴权参数",
                "API_KEY 主凭证与附加鉴权参数并存",
                AuthSpec(kind="apiKey",
                         requirements=[api_key_req("header", "X-API-Key", API_KEY),
                                       extra_param_req("header", "X-App-Key", EXTRA_APP_KEY)])),
        _simple("basic", "Basic 认证", "BASIC_AUTH",
                AuthSpec(kind="basic", requirements=[basic_req(BASIC_USER, BASIC_PASS)])),
        _simple("basic-extra", "Basic 认证 + 附加 Query",
                "BASIC_AUTH 与附加鉴权参数并存",
                AuthSpec(kind="basic",
                         requirements=[basic_req(BASIC_USER, BASIC_PASS),
                                       extra_param_req("query", "tenant", FIXED_TENANT)])),
        _oauth2_scenario("oauth2-post", "post", "OAuth2（client_secret_post）",
                         "OAUTH2 客户端凭证，凭证放表单体"),
        _oauth2_scenario("oauth2-basic", "basic", "OAuth2（client_secret_basic，令牌短寿）",
                         "OAUTH2 client_secret_basic + 令牌缓存失效后 401 重取并重放",
                         max_uses_key="oauth2BasicTokenMaxUses",
                         note="令牌按使用次数失效：默认第 3 次业务请求返回 401，"
                              "用于验证「401 后重取令牌并重放」"),
        _oauth2_scenario("oauth2-extra", "post", "OAuth2 + 附加鉴权参数",
                         "OAUTH2 与附加鉴权参数并存；令牌请求不带附加参数",
                         extra=extra_param_req("header", "X-App-Key", EXTRA_APP_KEY)),
        _oauth2_scenario("oauth2-expiring", "post", "OAuth2（令牌按时间过期）",
                         "OAUTH2 令牌缓存与过期刷新：距过期不足 30 秒应提前重取",
                         ttl_key="oauth2ExpiringTokenTtlSeconds",
                         note="令牌按时间过期：签发 expiresInSeconds 秒后业务请求返回 401，"
                              "用于验证「距过期不足 30 秒提前重取」"),
        _paged_oauth2_scenario(
            "oauth2-paged-maxuses", "OAuth2 分页（令牌按次数失效）",
            "一个批次内多次业务请求：复用令牌遇 401 → 重取 → 重放同页",
            max_uses_key="oauth2PagedTokenMaxUses",
            flow_note="每个令牌最多用于 2 次业务请求，第 3 次起 401（oauth2PagedTokenMaxUses 可调）",
            items_note="期望：一次完整同步 5 次成功的业务请求 → 令牌端点被请求 3 次、"
                       "业务请求出现 2 次 401 且每次之后紧跟一次同页重放成功、最终 50 条无重复"),
        _paged_oauth2_scenario(
            "oauth2-paged-expiring", "OAuth2 分页（令牌按时间过期 + 每页延迟）",
            "一个批次内令牌临近过期：后端应主动重取，不应出现 401",
            ttl_key="oauth2PagedTtlSeconds", delay_key="oauth2PagedDelaySeconds",
            # 参数是按后端的复用窗口算出来的：有效期 <=60 秒的短效令牌按**一半有效期**复用
            # （35 秒 → 17.5 秒后才重取），>60 秒的长效令牌才是「过期前 30 秒」。
            # 延迟 6 秒 → 5 页的发起时刻是 0/6/12/18/24 秒，第 4 页发起前 18 > 17.5 触发主动重取；
            # 全程 30 秒 < 有效期 35 秒，所以即使不重取也不会 401——「无 401」才干净地证明
            # 是主动重取，而不是撞了 401 之后重放救回来的。
            flow_note="令牌有效 35 秒（oauth2PagedTtlSeconds 可调，**别调过 60**：后端对长效令牌"
                      "换用「过期前 30 秒」规则）；每个业务请求服务端延迟 6 秒"
                      "（oauth2PagedDelaySeconds 可调）。短效令牌按一半有效期复用，"
                      "即 17.5 秒后才该重取",
            items_note="期望：令牌端点被请求 2 次、5 次业务请求全部 200、无任何 401、"
                       "最终 50 条无重复。5 页的发起时刻为 0/6/12/18/24 秒，第 4 页发起前"
                       "已过 18 秒（>17.5 的复用窗口）应触发主动重取；全程 30 秒短于有效期 35 秒，"
                       "所以「无 401」只可能来自主动重取，不是撞 401 后重放"),
        _simple("fixed-param", "固定参数鉴权（三个位置）",
                "FIXED_PARAM：Header + Query + Cookie 同时必填",
                AuthSpec(kind="fixedParam", requirements=[
                    api_key_req("header", "X-App-Id", FIXED_APP_ID),
                    api_key_req("query", "sign", FIXED_SIGN),
                    api_key_req("cookie", "tenant", FIXED_TENANT),
                ])),
        _simple("bearer-extra", "Bearer + 附加鉴权参数",
                "BEARER_TOKEN 与附加鉴权参数（Header + Query）并存",
                AuthSpec(kind="bearer", requirements=[
                    bearer_static_req(STATIC_TOKEN),
                    extra_param_req("header", "X-App-Key", EXTRA_APP_KEY),
                    extra_param_req("query", "sign", FIXED_SIGN),
                ])),
        _forbidden_scenario(),
        _token_body_scenario("token-json-body", JSON_MEDIA_TYPE,
                             "Bearer 取 Token（JSON 请求体，Token 在嵌套路径）",
                             "取 Token 接口只收 application/json；Token 路径 data.token.value 且带有效期字段"),
        _token_body_scenario("token-form-body", FORM_MEDIA_TYPE,
                             "Bearer 取 Token（表单请求体，Token 字段名非 access_token）",
                             "取 Token 接口只收 application/x-www-form-urlencoded；Token 在顶层 tokenValue"),
        # ---- 以下三个是 H-VERIFY-1013 的补测场景 ----
        _bearer_request_scenario(
            "token-typed-fields", "header", "Authorization", "Bearer",
            "Bearer 取 Token（返回里带布尔与小数字段）",
            "取 Token 返回除令牌（文本）与有效期（整数）外还有布尔、小数字段；"
            "有效期路径的候选不应包含布尔字段",
            extra_top=TYPED_TOKEN_EXTRA_TOP, extra_data=TYPED_TOKEN_EXTRA_DATA,
            extra_description="。返回里另有布尔字段 success、data.refreshable 与小数字段 "
                              "data.refreshRatio：令牌路径是 data.accessToken，"
                              "有效期（秒）路径是 data.expiresIn",
            flow_extra={"nonTextTokenResponseFields": {
                "boolean": ["success", "data.refreshable"],
                "number": ["data.refreshRatio"],
                "integer": ["code", "data.expiresIn"]}}),
        _oauth2_scenario("oauth2-scope-required", "post", "OAuth2（令牌接口校验授权范围）",
                         "OAUTH2 授权范围：令牌请求必须带 scope=read，否则 400 invalid_scope",
                         required_scope=REQUIRED_SCOPE,
                         note="令牌接口只接受 scope=%s：不带、为空或带别的值返回 400，"
                              "错误体 {\"error\": \"invalid_scope\", \"error_description\": ...}；"
                              "业务接口不再校验 scope" % REQUIRED_SCOPE),
        _bearer_request_scenario(
            "token-slow", "header", "Authorization", "Bearer",
            "Bearer 取 Token（取令牌接口延迟返回）",
            "取 Token 接口校验通过后固定延迟若干秒再返回，验证取令牌期间的等待与超时提示",
            delay_key="tokenSlowDelaySeconds",
            extra_description="。校验通过后延迟 %d 秒再返回（配置项 tokenSlowDelaySeconds，0~60），"
                              "账号密码错误立即返回 401、不延迟" % TOKEN_SLOW_DEFAULT_SECONDS,
            flow_extra={"tokenDelaySeconds": lambda: config_snapshot()["tokenSlowDelaySeconds"],
                        "note": "取令牌接口延迟返回，秒数由 /__admin/config 的 "
                                "tokenSlowDelaySeconds 调整（缺省 %d）" % TOKEN_SLOW_DEFAULT_SECONDS}),
    ]
    return scenarios
