# -*- coding: utf-8 -*-
"""A 类：鉴权场景。

每个场景一个独立前缀、一份独立数据（记录 id 带场景名，便于看出数据串场）。
业务接口统一是不分页的 `GET /items`，20 条，`{"code":0,"data":{"items":[...]}}`；
凭证缺失 / 错误 / 位置不对一律 401 + `{"code":401,"message":"..."}`。
"""

import base64
import threading

from .common import (
    Endpoint, Scenario, AuthSpec, ApiError,
    api_key_req, extra_param_req, bearer_static_req, basic_req, dynamic_token_req,
    build_dataset, json_resp, error_resp, issue_token,
    qp, hp, json_body, form_body,
)

# ==================== 测试凭证（全部是明显的假值） ====================

STATIC_TOKEN = "test-token-abc"
API_KEY = "test-key-123"
BASIC_USER, BASIC_PASS = "test", "test-pass"
LOGIN_USER, LOGIN_PASS = "test", "test-pass"
CLIENT_ID, CLIENT_SECRET = "test-client", "test-secret"
EXTRA_APP_KEY = "extra-key-1"
FIXED_APP_ID, FIXED_SIGN, FIXED_TENANT = "app-001", "test-sign", "t-001"

# 可由 POST /__admin/config 调整的参数
CONFIG = {
    "oauth2BasicTokenMaxUses": 2,        # oauth2-basic：令牌可用次数，第 N+1 次业务请求 401
    "oauth2ExpiringTokenTtlSeconds": 40,  # oauth2-expiring：令牌有效秒数
}
_CONFIG_LOCK = threading.Lock()


def apply_config(patch):
    """调整可变参数；未知键或非法值报 400"""
    with _CONFIG_LOCK:
        for key, value in (patch or {}).items():
            if key not in CONFIG:
                raise ApiError(400, "未知配置项：%s（可选：%s）" % (key, ", ".join(CONFIG)))
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ApiError(400, "配置项 %s 必须是不小于 1 的整数" % key)
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


# ==================== 登录 / 令牌端点 ====================

def _login_endpoint(scenario_name, inject_desc):
    """调用接口获取 Token 的登录端点。

    operationId / summary 用明显的写语义词（login），避免被后端判成读取接口而误勾选。
    """

    def handler(ctx):
        body = ctx.json_body_or_400()
        if body.get("username") != LOGIN_USER or body.get("password") != LOGIN_PASS:
            return error_resp(401, "登录失败：用户名或密码错误")
        token = issue_token(scenario_name)
        return json_resp({"code": 0, "message": "ok",
                          "data": {"accessToken": token, "expiresIn": 3600,
                                   "tokenType": "Bearer"}})

    return Endpoint(
        "POST", "/login", handler,
        operation_id="login", summary="登录并签发访问令牌（写操作，不要勾选为数据接口）",
        description="账号 %s / %s。令牌在 data.accessToken，%s" % (LOGIN_USER, LOGIN_PASS, inject_desc),
        role="TOKEN", auth=False,
        request_body=json_body(
            {"type": "object", "required": ["username", "password"],
             "properties": {"username": {"type": "string"}, "password": {"type": "string"}}},
            {"username": LOGIN_USER, "password": LOGIN_PASS}),
        doc_sample=None,
        response_schema={"type": "object", "properties": {
            "code": {"type": "integer"}, "message": {"type": "string"},
            "data": {"type": "object", "properties": {
                "accessToken": {"type": "string"},
                "expiresIn": {"type": "integer"},
                "tokenType": {"type": "string"}}}}},
        response_example={"code": 0, "message": "ok",
                          "data": {"accessToken": "tok-%s-1" % scenario_name,
                                   "expiresIn": 3600, "tokenType": "Bearer"}},
    )


def _oauth_token_endpoint(scenario_name, auth_method, reject_extra_header=False,
                          expiring=False):
    """OAuth2 客户端凭证令牌端点。

    auth_method=post  ：client_id / client_secret 放表单体；带了 Authorization 头 → 400
    auth_method=basic ：客户端凭证必须在 Authorization: Basic 头里；表单体出现 client_secret → 400
    expiring=True     ：签发的令牌按**时间**过期，`expires_in` 返回实际有效秒数
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
            max_uses = None
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
            max_uses = config_snapshot()["oauth2BasicTokenMaxUses"]

        scope = form.get("scope") or ""
        ttl = config_snapshot()["oauth2ExpiringTokenTtlSeconds"] if expiring else None
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

    return Endpoint(
        "POST", "/oauth/token", handler,
        operation_id="issueToken", summary="签发 OAuth2 访问令牌（写操作，不要勾选为数据接口）",
        description=desc, role="TOKEN", auth=False,
        request_body=form_body({"type": "object", "required": required, "properties": props}, example),
        doc_sample=None,
        response_schema={"type": "object", "properties": {
            "access_token": {"type": "string"}, "token_type": {"type": "string"},
            "expires_in": {"type": "integer"}, "scope": {"type": "string"}}},
        response_example={"access_token": "oat-%s-1" % scenario_name,
                          "token_type": "Bearer", "expires_in": 3600},
    )


# ==================== 场景定义 ====================

def _simple(name, title, covers, auth, extra_endpoints=()):
    rows = _dataset_for(name)
    endpoints = list(extra_endpoints) + [_items_endpoint(rows)]
    return Scenario(name=name, title=title, covers=covers, endpoints=endpoints, auth=auth)


def _bearer_request_scenario(name, location, header_name, prefix, title, covers):
    """Bearer「调用接口获取 Token」三种令牌注入位置"""
    inject = {"in": location.upper(), "name": header_name, "prefix": prefix}
    inject_desc = "注入到 %s %s%s" % (location.upper(), header_name,
                                     ("，带前缀 %s" % prefix) if prefix else "，不带前缀")
    auth = AuthSpec(
        kind="bearer",
        requirements=[dynamic_token_req(name, location, header_name, prefix=prefix,
                                        note=inject_desc)],
        flow={"kind": "REQUEST_TOKEN", "method": "POST", "urlPath": "/login",
              "contentType": "application/json",
              "requestBody": {"username": LOGIN_USER, "password": LOGIN_PASS},
              "tokenPath": "data.accessToken", "expiresInPath": "data.expiresIn",
              "inject": inject},
        message="未授权",
    )
    return _simple(name, title, covers, auth, [_login_endpoint(name, inject_desc)])


def _oauth2_scenario(name, auth_method, title, covers, extra=None, expiring=False):
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
    if auth_method == "basic":
        # 写成 callable：/__admin/config 改了之后清单要能反映出来
        flow["tokenMaxUses"] = lambda: config_snapshot()["oauth2BasicTokenMaxUses"]
        flow["note"] = "令牌按使用次数失效：默认第 3 次业务请求返回 401，用于验证「401 后重取令牌并重放」"
    if expiring:
        flow["expiresInSeconds"] = lambda: config_snapshot()["oauth2ExpiringTokenTtlSeconds"]
        flow["note"] = ("令牌按时间过期：签发 expiresInSeconds 秒后业务请求返回 401，"
                        "用于验证「距过期不足 30 秒提前重取」")
    auth = AuthSpec(kind="oauth2", requirements=reqs, flow=flow)
    token_ep = _oauth_token_endpoint(name, auth_method,
                                     reject_extra_header=(extra is not None),
                                     expiring=expiring)
    return _simple(name, title, covers, auth, [token_ep])


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
        Scenario("apikey-header", "API Key（请求头）", "API_KEY in=header；另提供 Swagger 2.0 文档",
                 [_items_endpoint(_dataset_for("apikey-header"))],
                 auth=AuthSpec(kind="apiKey",
                               requirements=[api_key_req("header", "X-Api-Key", API_KEY)]),
                 swagger2=True),
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
                         "OAUTH2 client_secret_basic + 令牌缓存失效后 401 重取并重放"),
        _oauth2_scenario("oauth2-extra", "post", "OAuth2 + 附加鉴权参数",
                         "OAUTH2 与附加鉴权参数并存；令牌请求不带附加参数",
                         extra=extra_param_req("header", "X-App-Key", EXTRA_APP_KEY)),
        _oauth2_scenario("oauth2-expiring", "post", "OAuth2（令牌按时间过期）",
                         "OAUTH2 令牌缓存与过期刷新：距过期不足 30 秒应提前重取",
                         expiring=True),
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
    ]
    return scenarios
