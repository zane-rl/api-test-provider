# -*- coding: utf-8 -*-
"""E 类：可按接口切换「正常 / 持续失败」的分支依赖场景 `depend-switchable`（无鉴权）。

四个读取接口构成分支依赖 A→B→C、A→D：
- A `GET /customers`：客户列表
- B `GET /customers/{customerId}/orders`：路径参数取 A 的 id
- C `GET /orders/{orderNo}/items`：路径参数取 B 的 order_no
- D `GET /addresses?customer_id=`：查询参数取 A 的 id

每个接口各有一个开关，由 `/__admin/faults?scenario=depend-switchable` 在运行期切换：
切到「持续失败」后一直返回指定状态码（默认 503），直到切回「正常」为止——
与 `faults` 场景里「按调用次数失败、之后自动成功」的 `/flaky` 不是一回事。
同时按接口记录自上次复位以来的调用次数（成功、失败分开），
用来证明「暂停之后没有再来调这个接口」。

开关与计数只在真实请求里生效：生成文档时（`ctx.doc_mode`）一律跳过，
所以文档内容与开关状态无关，200 响应 schema 仍由真实响应推断。
"""

import threading
from datetime import timedelta

from .common import (
    Endpoint, Scenario, NO_AUTH, ApiError,
    build_dataset, iso, BASE_TIME, json_resp, qp, pp,
)

NAME = "depend-switchable"

MODE_NORMAL = "normal"
MODE_FAIL = "fail"
DEFAULT_FAIL_STATUS = 503

# ==================== 数据集 ====================

SW_CUSTOMERS = build_dataset("SWC", 5)
_CITIES = ["上海", "杭州", "成都"]


def _seq_of(code):
    """从 `SWC-00003` 取出序号 3"""
    return int(code.rsplit("-", 1)[1])


def orders_of(customer_id):
    """订单条数公式：`序号 % 3 + 1`，取值 1~3 条。5 个客户依次 2/3/1/2/3，合计 11 条。"""
    seq = _seq_of(customer_id)
    return [
        {
            "order_no": "%s-O%d" % (customer_id, j),
            "customer_id": customer_id,
            "seq": seq,
            "line": j,
            "amount": round(100 + seq * 10 + j * 1.5, 2),
            "paid": (seq + j) % 2 == 0,
            "placed_at": iso(BASE_TIME + timedelta(days=seq, hours=j)),
        }
        for j in range(1, seq % 3 + 2)
    ]


def items_of(order):
    """明细条数公式：`(客户序号 + 订单行号) % 2 + 1`，取值 1~2 条。11 张订单合计 16 条。"""
    count = (order["seq"] + order["line"]) % 2 + 1
    return [
        {
            "item_no": "%s-I%d" % (order["order_no"], k),
            "order_no": order["order_no"],
            "sku": "SKU-%03d" % (order["seq"] * 10 + order["line"] + k),
            "quantity": order["seq"] + order["line"] + k,
            "unit_price": round(9.9 + k * 2.5, 2),
        }
        for k in range(1, count + 1)
    ]


def addresses_of(customer_id):
    """地址条数公式：`(序号 * 2) % 3`，取值 0~2 条。5 个客户依次 2/1/0/2/1，合计 6 条。"""
    seq = _seq_of(customer_id)
    return [
        {
            "address_id": "%s-A%d" % (customer_id, j),
            "customer_id": customer_id,
            "city": _CITIES[(seq + j) % len(_CITIES)],
            "detail": "测试路 %d 号" % (seq * 100 + j),
            "is_default": j == 1,
        }
        for j in range(1, (seq * 2) % 3 + 1)
    ]


_ORDERS_FULL = [o for c in SW_CUSTOMERS for o in orders_of(c["id"])]
_ORDER_BY_NO = {o["order_no"]: o for o in _ORDERS_FULL}


def _public_order(order):
    """对外的订单记录：去掉只给明细公式用的内部序号"""
    return {k: v for k, v in order.items() if k not in ("seq", "line")}


ALL_ORDERS = [_public_order(o) for o in _ORDERS_FULL]
ALL_ITEMS = [i for o in _ORDERS_FULL for i in items_of(o)]
ALL_ADDRESSES = [a for c in SW_CUSTOMERS for a in addresses_of(c["id"])]


# ==================== 开关与调用计数 ====================

# 接口的开关键（顺序即展示顺序）→ (方法, 路径模板)
SWITCH_KEYS = [
    ("customers", "GET", "/customers"),
    ("orders", "GET", "/customers/{customerId}/orders"),
    ("items", "GET", "/orders/{orderNo}/items"),
    ("addresses", "GET", "/addresses"),
]

_lock = threading.Lock()
_state = {}


def _fresh():
    return {"mode": MODE_NORMAL, "failStatus": DEFAULT_FAIL_STATUS, "succeeded": 0, "failed": 0}


def reset_switches():
    """全部开关还原为正常、调用计数归零"""
    with _lock:
        _state.clear()
        for key, _, _ in SWITCH_KEYS:
            _state[key] = _fresh()


reset_switches()


def switches_snapshot():
    with _lock:
        return [
            {"key": key, "method": method, "path": path,
             "mode": _state[key]["mode"], "failStatus": _state[key]["failStatus"],
             "calls": {"total": _state[key]["succeeded"] + _state[key]["failed"],
                       "succeeded": _state[key]["succeeded"],
                       "failed": _state[key]["failed"]}}
            for key, method, path in SWITCH_KEYS
        ]


def apply_switches(patch):
    """按请求体切换开关，返回切换后的快照。

    请求体是一个开关对象 `{"endpoint": 键, "mode": "normal" | "fail", "status": 状态码}`，
    或 `{"switches": [开关对象, ...]}`；`status` 可省，缺省 503。
    先整体校验再生效：有一项不合法就整体拒绝，不会只切一半。
    """
    if not isinstance(patch, dict):
        raise ApiError(400, "请求体必须是 JSON 对象")
    items = patch["switches"] if "switches" in patch else [patch]
    if not isinstance(items, list) or not items:
        raise ApiError(400, "switches 必须是非空数组")
    keys = [key for key, _, _ in SWITCH_KEYS]
    changes = []
    for item in items:
        if not isinstance(item, dict):
            raise ApiError(400, "开关项必须是 JSON 对象，收到：%r" % (item,))
        key, mode = item.get("endpoint"), item.get("mode")
        if key not in keys:
            raise ApiError(400, "endpoint 必须是 %s 之一，收到：%r" % (" / ".join(keys), key))
        if mode not in (MODE_NORMAL, MODE_FAIL):
            raise ApiError(400, "mode 必须是 %s 或 %s，收到：%r" % (MODE_NORMAL, MODE_FAIL, mode))
        status = item.get("status", DEFAULT_FAIL_STATUS)
        if isinstance(status, bool) or not isinstance(status, int) or not 400 <= status <= 599:
            raise ApiError(400, "status 必须是 400~599 的整数，收到：%r" % (status,))
        # 切回正常时失败状态码一并还原成缺省值，避免下次切失败时沿用上一次的
        changes.append((key, mode, status if mode == MODE_FAIL else DEFAULT_FAIL_STATUS))
    with _lock:
        for key, mode, status in changes:
            _state[key]["mode"] = mode
            _state[key]["failStatus"] = status
    return switches_snapshot()


def _enter(key):
    """处在「持续失败」时计一次失败并返回要用的状态码；正常时返回 None"""
    with _lock:
        state = _state[key]
        if state["mode"] != MODE_FAIL:
            return None
        state["failed"] += 1
        return state["failStatus"]


def _count(key, succeeded):
    with _lock:
        _state[key]["succeeded" if succeeded else "failed"] += 1


def _guarded(key, handler):
    """给处理函数套上开关与计数"""
    def wrapper(ctx):
        # 生成文档时不看开关、不计数：文档必须与运行期状态无关
        if ctx.doc_mode:
            return handler(ctx)
        status = _enter(key)
        if status is not None:
            return json_resp(
                {"code": status,
                 "message": "接口 %s 已被切到持续失败（/__admin/faults?scenario=%s 可切回）"
                            % (key, NAME)},
                status=status)
        try:
            resp = handler(ctx)
        except ApiError:
            _count(key, False)
            raise
        _count(key, resp.status < 400)
        return resp
    return wrapper


# ==================== 场景 ====================

def _page(rows):
    return json_resp({"code": 0, "message": "ok", "data": {"items": rows, "total": len(rows)}})


def build_scenario():
    def customers(ctx):
        return _page(SW_CUSTOMERS)

    def orders(ctx):
        customer_id = ctx.path_params.get("customerId")
        if not any(c["id"] == customer_id for c in SW_CUSTOMERS):
            return json_resp({"code": 404, "message": "客户不存在：%s" % customer_id}, status=404)
        return _page([_public_order(o) for o in orders_of(customer_id)])

    def items(ctx):
        order = _ORDER_BY_NO.get(ctx.path_params.get("orderNo"))
        if order is None:
            return json_resp({"code": 404, "message": "订单不存在：%s"
                                                      % ctx.path_params.get("orderNo")}, status=404)
        return _page(items_of(order))

    def addresses(ctx):
        customer_id = ctx.query.get("customer_id")
        if not customer_id:
            raise ApiError(400, "缺少必填参数 customer_id")
        if not any(c["id"] == customer_id for c in SW_CUSTOMERS):
            return _page([])
        return _page(addresses_of(customer_id))

    switch_note = "开关键 %%s，见 /__admin/faults?scenario=%s" % NAME

    endpoints = [
        Endpoint("GET", "/customers", _guarded("customers", customers),
                 operation_id="listSwitchableCustomers", summary="查询客户列表（A，依赖的根）",
                 description="不分页，共 %d 条" % len(SW_CUSTOMERS),
                 record_path="data.items", primary_key="id",
                 dataset=lambda: list(SW_CUSTOMERS), doc_sample={},
                 note=switch_note % "customers",
                 extra_catalog={"faultKey": "customers"}),
        Endpoint("GET", "/customers/{customerId}/orders", _guarded("orders", orders),
                 operation_id="listSwitchableOrders", summary="查询客户订单（B，依赖 A）",
                 description="路径参数 customerId 依赖 /customers 的 id；条数 = 序号 %% 3 + 1，"
                             "取值 1~3，全部客户合计 %d 条；客户不存在返回 404" % len(ALL_ORDERS),
                 parameters=[pp("customerId", description="客户 ID，来自 /customers 的 id 字段")],
                 record_path="data.items", primary_key="order_no",
                 dataset=lambda: list(ALL_ORDERS),
                 doc_sample={"path": {"customerId": "SWC-00001"}},
                 note="依赖 /customers.id；expectedTotal 为对全部父记录求和；" + switch_note % "orders",
                 extra_catalog={"faultKey": "orders",
                                "dependsOn": {"path": "/customers", "field": "id",
                                              "param": {"name": "customerId", "in": "PATH"}}}),
        Endpoint("GET", "/orders/{orderNo}/items", _guarded("items", items),
                 operation_id="listSwitchableOrderItems", summary="查询订单明细（C，依赖 B）",
                 description="路径参数 orderNo 依赖 /customers/{customerId}/orders 的 order_no；"
                             "条数 = (客户序号 + 订单行号) %% 2 + 1，取值 1~2，全部订单合计 %d 条；"
                             "订单不存在返回 404" % len(ALL_ITEMS),
                 parameters=[pp("orderNo", description="订单号，来自 /customers/{customerId}/orders "
                                                       "的 order_no 字段")],
                 record_path="data.items", primary_key="item_no",
                 dataset=lambda: list(ALL_ITEMS),
                 doc_sample={"path": {"orderNo": "SWC-00001-O1"}},
                 note="依赖 /customers/{customerId}/orders.order_no；expectedTotal 为对全部父记录求和；"
                      + switch_note % "items",
                 extra_catalog={"faultKey": "items",
                                "dependsOn": {"path": "/customers/{customerId}/orders",
                                              "field": "order_no",
                                              "param": {"name": "orderNo", "in": "PATH"}}}),
        Endpoint("GET", "/addresses", _guarded("addresses", addresses),
                 operation_id="listSwitchableAddresses", summary="查询客户地址（D，依赖 A）",
                 description="查询参数 customer_id 依赖 /customers 的 id；条数 = (序号 * 2) %% 3，"
                             "取值 0~2，全部客户合计 %d 条；缺 customer_id 返回 400，"
                             "客户不存在返回空列表" % len(ALL_ADDRESSES),
                 parameters=[qp("customer_id", required=True,
                                description="客户 ID，来自 /customers 的 id 字段")],
                 record_path="data.items", primary_key="address_id",
                 dataset=lambda: list(ALL_ADDRESSES),
                 doc_sample={"query": {"customer_id": "SWC-00001"}},
                 note="依赖 /customers.id；expectedTotal 为对全部父记录求和；"
                      + switch_note % "addresses",
                 extra_catalog={"faultKey": "addresses",
                                "dependsOn": {"path": "/customers", "field": "id",
                                              "param": {"name": "customer_id", "in": "QUERY"}}}),
    ]
    return Scenario(NAME, "可切换故障的分支依赖",
                    "分支依赖 A→B→C、A→D；按接口切换正常 / 持续失败，验证连续失败暂停与修复后恢复",
                    endpoints, auth=NO_AUTH,
                    description="四个读取接口构成分支依赖；每个接口可由 /__admin/faults 在运行期"
                                "切到持续失败（默认 503）或切回正常")
