# -*- coding: utf-8 -*-
"""C 类：请求与响应场景（除 required-business-param 用固定 API Key 外，均无鉴权）。

覆盖：Query / 请求体原样回显（验证 `+`、中文、空格的编码）、记录路径命中单对象、
业务成功码的多种写法、一个接口产出多个对象（嵌套数组子表）、
验证接口带「无来源的必填参数」（路径参数 + required Query）。
"""

from datetime import timedelta

from .auth import API_KEY
from .common import (
    Endpoint, Scenario, NO_AUTH, AuthSpec, ApiError,
    api_key_req, build_dataset, build_record, iso, BASE_TIME,
    json_resp, qp, pp, json_body, json_type_of,
)


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
            "types": {k: json_type_of(v) for k, v in body.items()},
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


# ==================== required-business-param：验证接口带无来源的必填参数 ====================

# 可查到的客户：只认这几个 id，未知 id 返回 404，用来证明路径参数的值确实被带上了
RBP_CUSTOMERS = build_dataset("RBP-CUS", 5)
RBP_SAMPLE_TENANT = "tenant-001"


def _rbp_record(customer, tenant):
    """单条客户档案：tenant 原样回显，E2E 可据此断言补填的测试值确实送达"""
    record = dict(customer)
    record["tenant"] = tenant
    return record


def _scenario_required_business_param():
    def handler(ctx):
        tenant = ctx.query.get("tenant")
        if not tenant:
            raise ApiError(400, "缺少必填参数 tenant（Query）")
        customer_id = ctx.path_params.get("customerId")
        customer = next((c for c in RBP_CUSTOMERS if c["id"] == customer_id), None)
        if customer is None:
            raise ApiError(404, "客户不存在：%s" % customer_id)
        return json_resp({"code": 0, "message": "ok",
                          "data": {"items": [_rbp_record(customer, tenant)]}})

    ep = Endpoint(
        "GET", "/customers/{customerId}", handler,
        operation_id="getCustomerProfile", summary="查询客户档案（按客户 ID 与租户）",
        description="路径参数 customerId 与必填 Query 参数 tenant 都没有默认值、也没有样例值，"
                    "且不来自任何前置接口；缺 tenant 返回 400，customerId 不存在返回 404。"
                    "可用 customerId：%s ~ %s；tenant 任意非空值，原样回显在记录里"
                    % (RBP_CUSTOMERS[0]["id"], RBP_CUSTOMERS[-1]["id"]),
        # 两个参数都刻意不给 example / default，避免被后端当成固定值
        parameters=[pp("customerId", description="客户 ID"),
                    qp("tenant", "string", required=True, description="租户编码")],
        record_path="data.items", primary_key="id",
        dataset=lambda: [_rbp_record(RBP_CUSTOMERS[0], RBP_SAMPLE_TENANT)],
        doc_sample={"path": {"customerId": RBP_CUSTOMERS[0]["id"]},
                    "query": {"tenant": RBP_SAMPLE_TENANT}},
        note="验证接口必填参数无来源：缺值时后端应返回 NEEDS_TEST_VALUES 且不发请求；"
             "若误发请求（不带 tenant）会收到 400",
        extra_catalog={"requiredTestValues": [
            {"in": "PATH", "name": "customerId", "testValue": RBP_CUSTOMERS[0]["id"]},
            {"in": "QUERY", "name": "tenant", "testValue": RBP_SAMPLE_TENANT},
        ]},
    )
    return Scenario("required-business-param", "验证接口带无来源的必填参数",
                    "验证连接前检查请求值是否齐全：路径参数与 required Query 无固定值 / 无依赖 / 无测试值时"
                    "返回 NEEDS_TEST_VALUES 且不发请求",
                    [ep], auth=AuthSpec(kind="apiKey",
                                        requirements=[api_key_req("header", "X-Api-Key", API_KEY)]))


def build_scenarios():
    return [
        _scenario_echo(),
        _scenario_single_object(),
        _scenario_biz_code(),
        _scenario_nested(),
        _scenario_required_business_param(),
    ]
