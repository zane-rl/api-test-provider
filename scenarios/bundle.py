# -*- coding: utf-8 -*-
"""按鉴权类型合并的 OpenAPI 文档（给其他项目一次性读取某类鉴权下的全部接口）。

**不另写一份文档**：逐场景调用 `build_openapi` 拿到与实现一致的文档，只做改名合并——

- 路径加场景前缀：`/items` → `/s/<场景>/items`，`servers` 改为服务根地址；
- securitySchemes 按场景改名 `<场景>.<原名>`（各场景的同名 scheme 配置不同），
  顶层 security 下放到每个 operation；令牌 / 登录端点保持 `security: []`；
- components.parameters（公共参数）同样改名 `<场景>.<原名>`，引用它的 `$ref` 跟着改、**不内联**，
  路径项级的 parameters 原样保留在路径项上；
- operationId 加场景前缀保证全文唯一，原值留在 `x-original-operation-id`；
- 每个场景一个 tag，tag 的 `x-auth` 给出测试凭证与令牌流程（同 `/__admin/scenarios`）；
  每个 operation 的 `x-test` 给出角色、分页、记录路径、主键、期望条数。
"""

import copy
from collections import OrderedDict

from . import REGISTRY
from .openapi import ERROR_SCHEMA, HTTP_METHODS, build_openapi

ALL = "all"

# 鉴权类型的中文名与说明（键即 AuthSpec.kind）
KIND_TITLES = {
    "none": ("无鉴权", "不需要任何凭证；分页、请求响应、依赖、故障、类型不一致类场景都在这里"),
    "bearer": ("Bearer Token", "固定令牌，或先调登录接口取令牌再注入 Header / Query"),
    "apiKey": ("API Key", "Header / Query / Cookie 三种位置，含前缀与附加鉴权参数"),
    "basic": ("HTTP Basic", "用户名 + 密码，含附加鉴权参数"),
    "oauth2": ("OAuth2 客户端凭证", "client_credentials；client_secret_post / basic、令牌按次数或按时间失效"),
    "fixedParam": ("固定参数", "Header + Query + Cookie 三处同时带固定值"),
}


def auth_kinds():
    """注册表里出现的鉴权类型，按场景首次出现的顺序"""
    kinds = OrderedDict()
    for scenario in REGISTRY.values():
        kinds.setdefault(scenario.auth.kind, None)
    return list(kinds)


def scenarios_of(kind):
    if kind == ALL:
        return list(REGISTRY.values())
    return [s for s in REGISTRY.values() if s.auth.kind == kind]


def _camel(name):
    parts = [p for p in name.replace("_", "-").split("-") if p]
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _test_meta(endpoint, scenario_base):
    """operation 的 x-test：取自清单，去掉与 OpenAPI 本身重复的字段"""
    item = endpoint.catalog(scenario_base)
    for key in ("method", "path", "url", "summary", "sampleFirst3"):
        item.pop(key, None)
    return item


PARAM_REF_PREFIX = "#/components/parameters/"


def rename_param_refs(parameters, scenario_name):
    """把参数列表里指向公共参数的 $ref 改成合并文档里的名字 `<场景>.<原名>`"""
    out = []
    for param in parameters:
        ref = param.get("$ref", "")
        if ref.startswith(PARAM_REF_PREFIX):
            param = {"$ref": "%s%s.%s" % (PARAM_REF_PREFIX, scenario_name,
                                          ref[len(PARAM_REF_PREFIX):])}
        out.append(param)
    return out


def build_bundle(kind, root_base):
    """合并 kind 类鉴权的全部场景；kind 为 "all" 时合并全部场景"""
    scenarios = scenarios_of(kind)
    if not scenarios:
        raise KeyError(kind)

    paths = OrderedDict()
    schemes = OrderedDict()
    shared_params = OrderedDict()
    tags = []
    for scenario in scenarios:
        scenario_base = "%s/s/%s" % (root_base, scenario.name)
        doc = copy.deepcopy(build_openapi(scenario, scenario_base))

        renamed = {}
        for name, scheme in doc.get("components", {}).get("securitySchemes", {}).items():
            renamed[name] = "%s.%s" % (scenario.name, name)
            schemes[renamed[name]] = scheme
        security = [{renamed[k]: v for k, v in req.items()} for req in doc.get("security", [])]
        for name, param in doc.get("components", {}).get("parameters", {}).items():
            shared_params["%s.%s" % (scenario.name, name)] = param

        tags.append({
            "name": scenario.name,
            "description": "%s（%s）：%s" % (scenario.title, getattr(scenario, "group", ""),
                                           scenario.covers),
            "x-auth": scenario.auth.describe(scenario_base),
            "x-openapi-url": scenario_base + "/openapi.json",
        })

        endpoints = {(e.path, e.method.lower()): e for e in scenario.endpoints}
        for path, operations in doc["paths"].items():
            target = paths.setdefault("/s/%s%s" % (scenario.name, path), OrderedDict())
            for method, op in operations.items():
                if method not in HTTP_METHODS:
                    # 路径项级 parameters：留在路径项上，只改引用名
                    target[method] = (rename_param_refs(op, scenario.name)
                                      if method == "parameters" else op)
                    continue
                endpoint = endpoints[(path, method)]
                if "parameters" in op:
                    op["parameters"] = rename_param_refs(op["parameters"], scenario.name)
                op["x-original-operation-id"] = op["operationId"]
                op["operationId"] = "%s_%s" % (_camel(scenario.name), op["operationId"])
                op["tags"] = [scenario.name]
                if "security" not in op and security:
                    op["security"] = copy.deepcopy(security)
                op["x-scenario"] = scenario.name
                op["x-auth-type"] = scenario.auth.kind
                op["x-test"] = _test_meta(endpoint, scenario_base)
                target[method] = op

    if kind == ALL:
        title, intro = "全部场景", "全部鉴权类型的场景合在一份文档里"
    else:
        title, intro = KIND_TITLES.get(kind, (kind, ""))
    endpoint_count = sum(1 for ops in paths.values() for m in ops if m in HTTP_METHODS)
    description = "\n".join([
        "按鉴权类型合并的测试接口文档：%s（%d 个场景 / %d 个端点）。" % (intro, len(scenarios), endpoint_count),
        "",
        "- 每个场景一个 tag；tag 的 x-auth 是该场景的测试凭证与令牌流程。",
        "- 鉴权挂在每个 operation 的 security 上，scheme 名为 <场景>.<原名>。",
        "- x-test.role：DATA 业务接口 / TOKEN 登录与令牌接口（不要当数据接口调）/ FAULT 故障注入。",
        "- 每次测试前 POST %s/__admin/reset?scenario=<场景> 复位，调用后查 %s/__admin/requests。"
        % (root_base, root_base),
    ])
    bundle = OrderedDict([
        ("openapi", "3.0.3"),
        ("info", {
            "title": "测试 API 提供服务 · 鉴权：%s" % title,
            "version": "1.0.0",
            "description": description,
        }),
        ("servers", [{"url": root_base}]),
        ("tags", tags),
        ("paths", paths),
        ("components", {"schemas": {"Error": copy.deepcopy(ERROR_SCHEMA)}}),
    ])
    if shared_params:
        bundle["components"]["parameters"] = shared_params
    if schemes:
        bundle["components"]["securitySchemes"] = schemes
    bundle["x-auth-type"] = kind
    return bundle


def bundle_names():
    """可用的合并文档名：各鉴权类型 + all"""
    return auth_kinds() + [ALL]
