# -*- coding: utf-8 -*-
"""OpenAPI 文档生成。

**文档不是手写的**：paths 由端点注册表遍历生成，200 响应 schema 由「真的调用一次处理函数」
推断（端点通过 `doc_sample` 声明文档期用的 query / body / path）。
有副作用的端点（故障注入、令牌签发）显式声明 `response_schema`，不调用处理函数。

正常场景的文档一律是 OpenAPI 3.0.3。Swagger 2.0 转换只给 `scenarios/negative.py` 的
反例文档用（产品口径不支持 Swagger 2.0，后端应拒绝导入）。
"""

import json
import re
import threading
from urllib.parse import urlparse

from .common import Ctx, infer_schema, truncate_example

_cache = {}
_cache_lock = threading.Lock()

# 路径项（path item）里除了 HTTP 方法还可能有 parameters 等键，遍历操作时只认这些
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

ERROR_SCHEMA = {
    "type": "object",
    "properties": {"code": {"type": "integer"}, "message": {"type": "string"}},
    "description": "错误响应体",
}


def _doc_ctx(scenario, endpoint, base_url):
    """生成文档时用的假上下文：处理函数与线上跑的是同一个，所以 schema 必然等于实现"""
    sample = endpoint.doc_sample or {}
    query_all = {k: [str(v)] for k, v in (sample.get("query") or {}).items()}
    body = sample.get("body")
    if isinstance(body, (dict, list)):
        body_raw = json.dumps(body, ensure_ascii=False)
    else:
        body_raw = body or ""
    ctx = Ctx(scenario.name, endpoint.method, base_url + endpoint.path,
              sample.get("path") or {}, query_all, sample.get("headers") or {},
              body_raw, base_url)
    ctx.doc_mode = True
    return ctx


def _response_of(scenario, endpoint, base_url):
    """返回 (schema, example)"""
    if endpoint.response_schema is not None:
        return endpoint.response_schema, endpoint.response_example
    if endpoint.doc_sample is None:
        raise RuntimeError("端点 %s %s 既未声明 response_schema，也未给出 doc_sample"
                           % (endpoint.method, endpoint.path))
    resp = endpoint.handler(_doc_ctx(scenario, endpoint, base_url))
    if resp.payload is None:
        raise RuntimeError("端点 %s %s 的样本响应没有 JSON 体" % (endpoint.method, endpoint.path))
    return infer_schema(resp.payload), truncate_example(resp.payload)


def build_openapi(scenario, base_url):
    """生成 OpenAPI 3.0.3 文档"""
    key = ("v3", scenario.name, base_url)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    paths = {}
    for endpoint in scenario.endpoints:
        schema, example = _response_of(scenario, endpoint, base_url)
        content = {"schema": schema}
        if example is not None:
            content["example"] = example
        operation = {
            "operationId": endpoint.operation_id,
            "summary": endpoint.summary,
            "description": endpoint.description,
            "responses": {
                "200": {"description": "成功", "content": {"application/json": content}},
                "default": {"description": "错误响应（参数、鉴权或业务错误）",
                            "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}}}},
            },
        }
        if endpoint.parameters:
            operation["parameters"] = endpoint.parameters
        if endpoint.request_body:
            operation["requestBody"] = endpoint.request_body
        if not endpoint.auth:
            operation["security"] = []      # 登录 / 令牌端点本身不需要鉴权
        elif scenario.auth.requirements:
            operation["responses"]["401"] = {
                "description": "凭证缺失或错误",
                "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Error"}}}}
        path_item = paths.setdefault(endpoint.path, {})
        if endpoint.path_parameters:
            # 路径项级参数：与 get / post 并列的 parameters 键，对该路径下所有操作生效
            path_item["parameters"] = endpoint.path_parameters
        path_item[endpoint.method.lower()] = operation

    doc = {
        "openapi": "3.0.3",
        "info": {
            "title": "测试 API 提供服务 · %s" % scenario.title,
            "version": "1.0.0",
            "description": "%s\n\n覆盖的后端能力：%s" % (scenario.description, scenario.covers),
        },
        "servers": [{"url": base_url}],
        "paths": paths,
        "components": {"schemas": {"Error": ERROR_SCHEMA}},
    }
    if scenario.shared_parameters:
        doc["components"]["parameters"] = scenario.shared_parameters
    schemes = scenario.auth.schemes()
    if schemes:
        doc["components"]["securitySchemes"] = schemes
        doc["security"] = scenario.auth.security()

    # securityScheme 里的 {base} 占位（OAuth2 tokenUrl）在这里替换成实际地址
    doc = json.loads(json.dumps(doc, ensure_ascii=False).replace("{base}", base_url))

    with _cache_lock:
        _cache[key] = doc
    return doc


# ==================== Swagger 2.0 转换（仅反例文档使用） ====================

def _strip_v3_only(schema):
    """v2 不认识 nullable / oneOf 等；递归去掉 nullable"""
    if isinstance(schema, list):
        return [_strip_v3_only(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k == "nullable":
            continue
        out[k] = _strip_v3_only(v)
    return out


def _v2_security_definitions(schemes):
    out = {}
    for name, scheme in (schemes or {}).items():
        kind = scheme.get("type")
        desc = scheme.get("description", "")
        if kind == "apiKey":
            out[name] = {"type": "apiKey", "name": scheme["name"], "in": scheme["in"],
                         "description": desc}
        elif kind == "http" and scheme.get("scheme") == "basic":
            out[name] = {"type": "basic", "description": desc}
        elif kind == "http" and scheme.get("scheme") == "bearer":
            # Swagger 2.0 没有 bearer 方案，按 apiKey in header 表达（值含 Bearer 前缀）
            out[name] = {"type": "apiKey", "name": "Authorization", "in": "header",
                         "description": (desc + "；值须带 `Bearer ` 前缀").strip("；")}
        elif kind == "oauth2":
            flow = (scheme.get("flows") or {}).get("clientCredentials") or {}
            out[name] = {"type": "oauth2", "flow": "application",
                         "tokenUrl": flow.get("tokenUrl", ""),
                         "scopes": flow.get("scopes", {}), "description": desc}
    return out


def _v2_parameters(operation):
    params = []
    for p in operation.get("parameters", []):
        schema = p.get("schema") or {}
        item = {"name": p["name"], "in": p["in"], "required": bool(p.get("required")),
                "description": p.get("description", ""), "type": schema.get("type", "string")}
        if schema.get("format"):
            item["format"] = schema["format"]
        params.append(item)
    body = operation.get("requestBody")
    if body:
        content = body.get("content", {})
        if "application/json" in content:
            params.append({"name": "body", "in": "body",
                           "required": bool(body.get("required")),
                           "description": "请求体",
                           "schema": _strip_v3_only(content["application/json"]["schema"])})
        elif "application/x-www-form-urlencoded" in content:
            schema = content["application/x-www-form-urlencoded"]["schema"]
            required = set(schema.get("required") or [])
            for field, field_schema in (schema.get("properties") or {}).items():
                params.append({"name": field, "in": "formData",
                               "required": field in required,
                               "type": field_schema.get("type", "string"),
                               "description": ""})
    return params


def build_swagger2(scenario, base_url):
    """把 v3 文档转成 Swagger 2.0 写法（host / basePath / definitions / securityDefinitions）"""
    key = ("v2", scenario.name, base_url)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    v3 = build_openapi(scenario, base_url)
    parsed = urlparse(base_url)
    definitions = {}
    paths = {}
    for path, methods in v3["paths"].items():
        for method, operation in methods.items():
            if method not in HTTP_METHODS:
                continue
            op_id = operation["operationId"]
            ref_name = op_id[0].upper() + op_id[1:] + "Response"
            schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
            definitions[ref_name] = _strip_v3_only(schema)
            v2_op = {
                "operationId": operation["operationId"],
                "summary": operation["summary"],
                "description": operation["description"],
                "produces": ["application/json"],
                "responses": {"200": {"description": "成功",
                                      "schema": {"$ref": "#/definitions/%s" % ref_name}}},
            }
            params = _v2_parameters(operation)
            if params:
                v2_op["parameters"] = params
                if any(p["in"] in ("body", "formData") for p in params):
                    v2_op["consumes"] = ["application/json" if any(p["in"] == "body" for p in params)
                                         else "application/x-www-form-urlencoded"]
            if "401" in operation["responses"]:
                definitions.setdefault("Error", _strip_v3_only(ERROR_SCHEMA))
                v2_op["responses"]["401"] = {"description": "凭证缺失或错误",
                                             "schema": {"$ref": "#/definitions/Error"}}
            if "security" in operation:
                v2_op["security"] = operation["security"]
            paths.setdefault(path, {})[method] = v2_op

    doc = {
        "swagger": "2.0",
        "info": v3["info"],
        "host": parsed.netloc,
        "basePath": parsed.path or "/",
        "schemes": [parsed.scheme],
        "consumes": ["application/json"],
        "produces": ["application/json"],
        "paths": paths,
        "definitions": definitions,
    }
    if "securitySchemes" in v3.get("components", {}):
        doc["securityDefinitions"] = _v2_security_definitions(v3["components"]["securitySchemes"])
        doc["security"] = v3.get("security", [])

    with _cache_lock:
        _cache[key] = doc
    return doc


def clear_cache():
    with _cache_lock:
        _cache.clear()
