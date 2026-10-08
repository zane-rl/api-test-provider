# -*- coding: utf-8 -*-
"""反例文档：应被后端拒绝的文档版本（Swagger 2.0、OpenAPI 3.1.0）。

产品口径是只支持 OpenAPI 3.0.x。这里单独提供两份「不在支持范围内」的文档，给后端验证
「收紧文档输入范围后拒绝导入」用。它们**不是正常场景**：不进 `REGISTRY`、不计入场景数、
不进按鉴权类型合并的文档，路径统一在 `/__negative/` 下。

两份文档描述的是同一个最简单的接口 `GET /__negative/items`（真实可请求，恒定 3 条），
都由同一份 3.0.3 文档转换而来，不手写。
"""

import copy
from collections import OrderedDict

from .common import Endpoint, Scenario, NO_AUTH, json_resp
from .openapi import build_openapi, build_swagger2

NAME = "__negative"
PREFIX = "/__negative"

# 字段刻意只用最朴素的类型（没有 null），这样改写成 3.1.0 后仍是一份合法的 3.1 文档
ROWS = [{"id": "NEG-%05d" % i, "name": "反例物料%d" % i, "quantity": i * 13} for i in range(1, 4)]


def _handler(ctx):
    return json_resp({"code": 0, "message": "ok", "data": {"items": ROWS}})


SCENARIO = Scenario(
    NAME, "应被拒绝的文档版本（反例）",
    "文档版本不在支持范围（Swagger 2.0 / OpenAPI 3.1+）时应拒绝导入",
    [Endpoint("GET", "/items", _handler,
              operation_id="listNegativeItems", summary="查询数据项（反例文档用的最简接口）",
              description="不分页，一次返回全部 %d 条" % len(ROWS),
              record_path="data.items", primary_key="id",
              dataset=lambda: list(ROWS), doc_sample={})],
    auth=NO_AUTH)


def _openapi31(base_url):
    """把 3.0.3 文档的版本号改写成 3.1.0（内容本身不含 3.0 专有写法）"""
    doc = copy.deepcopy(build_openapi(SCENARIO, base_url))
    doc["openapi"] = "3.1.0"
    return doc


# 文档名 -> (版本说明, 生成函数, 推荐使用的扩展名)
DOCUMENTS = OrderedDict([
    ("swagger2", ("Swagger 2.0", lambda base: build_swagger2(SCENARIO, base), "json")),
    ("openapi31", ("OpenAPI 3.1.0", _openapi31, "yaml")),
])


def build_document(name, root_base):
    return DOCUMENTS[name][1](root_base + PREFIX)


def catalog(root_base, extensions):
    """写进 /__admin/scenarios 的 negativeDocuments"""
    base = root_base + PREFIX
    endpoint = SCENARIO.endpoints[0]
    items = []
    for name, (version, _, preferred) in DOCUMENTS.items():
        items.append({
            "name": name,
            "specVersion": version,
            "expectedBackendSupport": "REJECTED",
            "url": "%s/%s.%s" % (base, name, preferred),
            "urls": {ext: "%s/%s.%s" % (base, name, ext) for ext in extensions},
            "endpoint": {"method": endpoint.method, "url": base + endpoint.path,
                         "recordPath": endpoint.record_path,
                         "primaryKey": endpoint.primary_key,
                         "expectedTotal": endpoint.expected_total()},
        })
    return items
