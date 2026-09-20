# -*- coding: utf-8 -*-
"""C 类：请求与响应场景（均无鉴权）。

覆盖：Query / 请求体原样回显（验证 `+`、中文、空格的编码）、记录路径命中单对象、
业务成功码的多种写法、一个接口产出多个对象（嵌套数组子表）。
"""

from datetime import timedelta

from .common import (
    Endpoint, Scenario, NO_AUTH, ApiError,
    build_dataset, build_record, iso, BASE_TIME,
    json_resp, qp, json_body,
)


def _json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


# ==================== echo：请求原样回显 ====================

def _scenario_echo():
    def echo_query(ctx):
        query = ctx.query
        record = {
            "id": "ECHO-00001",
            # 服务端解码后的取值：`+` 若未被编码为 %2B，这里会变成空格，一眼可见
            "since": query.get("since"),
            "q": query.get("q"),
            "received_query": query,
            "raw_query": ctx.raw_query,
            "param_count": len(query),
        }
        return json_resp({"code": 0, "message": "ok", "data": {"items": [record]}})

    def echo_body(ctx):
        body = ctx.json_body_or_400()
        record = {
            "id": "ECHOBODY-00001",
            "received_body": body,
            "raw_body": ctx.body_raw,
            "types": {k: _json_type(v) for k, v in body.items()},
        }
        return json_resp({"code": 0, "message": "ok", "data": {"items": [record]}})

    endpoints = [
        Endpoint("GET", "/echo", echo_query,
                 operation_id="listEchoedQuery", summary="回显收到的查询参数",
                 description="把服务端解码后收到的每个 Query 值原样放进唯一一条记录，"
                             "用于验证 `+` 被编码为 %2B、中文与空格正确",
                 parameters=[qp("since", "string", description="带时区的时间，例 2026-09-10T14:30:00+08:00"),
                             qp("q", "string", description="任意文本，可含中文与空格")],
                 record_path="data.items", primary_key="id", expected_total=1,
                 doc_sample={"query": {"since": "2026-09-10T14:30:00+08:00", "q": "中文 关键词"}}),
        Endpoint("POST", "/echo-body", echo_body,
                 operation_id="listEchoedBody", summary="回显收到的请求体",
                 description="把收到的 JSON 体原样作为一条记录返回，并附每个顶层值的 JSON 类型",
                 request_body=json_body({"type": "object", "additionalProperties": True},
                                        {"page": 1, "size": 20, "active": True,
                                         "keyword": "中文 关键词", "extra": None}),
                 record_path="data.items", primary_key="id", expected_total=1,
                 doc_sample={"body": {"page": 1, "size": 20, "active": True,
                                      "keyword": "中文 关键词", "extra": None}}),
    ]
    return Scenario("echo", "请求回显", "Query / 请求体编码与类型保真验证", endpoints, auth=NO_AUTH)


# ==================== single-object：记录路径命中单对象 ====================

PROFILE = {
    "id": "PROFILE-00001",
    "tenant": "t-001",
    "display_name": "测试租户档案",
    "quota": 5000,
    "usage_ratio": 0.375,
    "enabled": True,
    "created_at": iso(BASE_TIME),
    "expired_at": None,
    "owner": {"id": "OP-01", "name": "操作员1", "email": "op1@example.test"},
}


def _scenario_single_object():
    def handler(ctx):
        return json_resp({"code": 0, "message": "ok", "data": PROFILE})

    ep = Endpoint(
        "GET", "/profile", handler,
        operation_id="getProfile", summary="查询租户档案（单个对象）",
        description="记录路径命中单个对象，应产生 1 条记录",
        record_path="data", primary_key="id", dataset=lambda: [PROFILE],
        doc_sample={},
    )
    return Scenario("single-object", "记录路径命中单对象", "记录路径 × 单对象 → 1 条记录",
                    [ep], auth=NO_AUTH)


# ==================== biz-code：业务成功条件 ====================

BIZ_ROWS = build_dataset("BIZ", 3)


def _scenario_biz_code():
    def ok_zero(ctx):
        return json_resp({"code": 0, "message": "ok", "data": {"items": BIZ_ROWS}})

    def ok_200(ctx):
        return json_resp({"code": 200, "message": "ok", "data": {"items": BIZ_ROWS}})

    def ok_string(ctx):
        return json_resp({"code": "0", "message": "ok", "data": {"items": BIZ_ROWS}})

    def biz_fail(ctx):
        return json_resp({"code": 5001, "message": "库存服务繁忙", "data": None})

    def make(path, handler, op, summary, desc, total, sample_note=""):
        return Endpoint("GET", path, handler, operation_id=op, summary=summary,
                        description=desc, record_path="data.items", primary_key="id",
                        dataset=(lambda: list(BIZ_ROWS)) if total else None,
                        expected_total=None if total else 0,
                        doc_sample={}, note=sample_note)

    endpoints = [
        make("/ok-zero", ok_zero, "listOkZero", "查询数据（业务码 0）",
             "HTTP 200 且 code 为数值 0，业务成功", True),
        make("/ok-200", ok_200, "listOk200", "查询数据（业务码 200）",
             "HTTP 200 且 code 为数值 200，用于验证成功条件的多个允许值", True),
        make("/ok-string", ok_string, "listOkString", "查询数据（业务码字符串 \"0\"）",
             "code 是字符串 \"0\"，用于验证按类型匹配", True),
        make("/biz-fail", biz_fail, "listBizFail", "查询数据（业务失败）",
             "HTTP 200 但 code 为 5001，应判业务失败", False,
             "HTTP 200 业务失败，期望后端判失败"),
    ]
    return Scenario("biz-code", "业务成功条件", "成功条件 EQ/NE × 数值/字符串 × 多允许值",
                    endpoints, auth=NO_AUTH)


# ==================== nested：一接口多对象 ====================

def _build_orders():
    orders = []
    for i in range(1, 13):
        line_count = (i % 3) + 1          # 每单 1~3 行，条数可预期
        orders.append({
            "order_no": "ORD-%05d" % i,
            "status": ["NEW", "PAID", "SHIPPED"][i % 3],
            "amount": round(100 + i * 13.7, 2),
            "urgent": i % 4 == 0,
            "created_at": iso(BASE_TIME + timedelta(days=i)),
            "cancelled_at": None if i % 5 else iso(BASE_TIME + timedelta(days=i, hours=3)),
            "customer": {"id": "CUS-%05d" % ((i % 6) + 1),
                         "name": "客户%d" % ((i % 6) + 1),
                         "level": ["VIP", "NORMAL"][i % 2]},
            "lines": [{"line_no": j,
                       "sku": "PRD-%05d" % ((i * 3 + j) % 57 + 1),
                       "quantity": j * 2 + i % 4,
                       "price": round(9.9 + (i + j) * 1.37, 2)}
                      for j in range(1, line_count + 1)],
        })
    return orders


ORDERS = _build_orders()
ORDER_LINES = [dict(line, order_no=o["order_no"]) for o in ORDERS for line in o["lines"]]


def _scenario_nested():
    def handler(ctx):
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": ORDERS, "total": len(ORDERS)}})

    ep = Endpoint(
        "GET", "/orders", handler,
        operation_id="listOrders", summary="查询订单列表（含嵌套子表）",
        description="每条订单含对象字段 customer 与嵌套数组 lines（每单 1~3 行），"
                    "用于验证一个接口产出多个数据对象",
        record_path="data.items", primary_key="order_no", dataset=lambda: list(ORDERS),
        pagination={"type": "NONE"}, doc_sample={},
        note="子表 data.items[].lines 共 %d 行（主键 order_no + line_no）" % len(ORDER_LINES),
    )
    return Scenario("nested", "嵌套对象与子表", "对象字段展开 + 嵌套数组子表（子表复制父字段）",
                    [ep], auth=NO_AUTH)


def build_scenarios():
    return [
        _scenario_echo(),
        _scenario_single_object(),
        _scenario_biz_code(),
        _scenario_nested(),
    ]
